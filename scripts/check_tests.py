#!/usr/bin/env python3
"""Fewest-tests check: run pytest once with per-test coverage, then
1. FAIL if any changed line or branch in --src code is not run by a test;
2. WARN for each new test that covers no --src line that other tests miss,
   unless its def line (or the line above) carries `# test-reason: ...`.
Usage (repo root): python scripts/check_tests.py [--base REF] [--src DIR ...] [--strict] [-- PYTEST_ARGS]
Needs pytest + pytest-cov. Exit 0 pass, 1 uncovered change (or warning with --strict), 2 tests failed.
Lines marked `# pragma: no cover` are exempt (give the reason on the same line).
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

OUT = Path(".check_tests")


def sh(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def is_test_file(p):
    n = Path(p).name
    return n.startswith("test_") or n.endswith("_test.py") or n == "conftest.py"


def changed_lines(base):
    """{repo-relative .py path: set(new line numbers)} for base..working tree, incl. untracked files."""
    out, cur = {}, None
    for ln in sh("git", "diff", "-U0", "--no-color", "--no-ext-diff", base, "--", "*.py").splitlines():
        if ln.startswith("+++ "):
            cur = None if ln.endswith("/dev/null") else ln[6:]
        elif ln.startswith("@@") and cur:
            m = re.search(r"\+(\d+)(?:,(\d+))?", ln)
            s, n = int(m[1]), int(m[2] if m[2] is not None else 1)
            out.setdefault(cur, set()).update(range(s, s + n))
    for p in sh("git", "ls-files", "--others", "--exclude-standard", "--", "*.py").splitlines():
        out[p] = set(range(1, len(Path(p).read_text().splitlines()) + 1))
    return out


def under(p, srcs):
    rp = Path(p).resolve()
    return any(rp == s or s in rp.parents for s in srcs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--src", nargs="+", default=["."], help="code that must be covered (package dirs)")
    ap.add_argument("--strict", action="store_true", help="exit 1 on redundant-test warnings too")
    a, pytest_args = ap.parse_known_args()
    pytest_args = [x for x in pytest_args if x != "--"]
    try:
        sh("git", "rev-parse", "--verify", a.base + "^{commit}")
    except subprocess.CalledProcessError:
        sys.exit(f"check_tests: base ref {a.base!r} not found; pass --base REF")
    root = Path(sh("git", "rev-parse", "--show-toplevel").strip())
    os.chdir(root)
    OUT.mkdir(exist_ok=True)
    srcs = [Path(s).resolve() for s in a.src]
    env = dict(os.environ, COVERAGE_FILE=str(OUT / ".coverage"))
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov-branch",
           "--cov-context=test", f"--cov-report=json:{OUT / 'cov.json'}",
           *[f"--cov={s}" for s in srcs], *pytest_args]
    r = subprocess.run(cmd, env=env)
    if r.returncode != 0:
        print(f"check_tests: pytest exited {r.returncode}; fix failing tests first")
        return 2
    rep = {str(Path(k).resolve()): v for k, v in json.loads((OUT / "cov.json").read_text())["files"].items()}
    chg = changed_lines(a.base)

    # 1. changed lines/branches not run by any test
    bad = []
    for p, lines in sorted(chg.items()):
        rp = str((root / p).resolve())
        if is_test_file(p) or not under(rp, srcs) or not Path(rp).exists():
            continue
        f = rep.get(rp)
        if f is None:
            bad.append(f"{p}: changed, but no test imports this file")
            continue
        miss = sorted(set(f["missing_lines"]) & lines)
        mbr = sorted((s, t) for s, t in f.get("missing_branches", []) if s in lines and s not in miss)
        if miss:
            bad.append(f"{p}: changed lines never run: {miss}")
        if mbr:
            bad.append(f"{p}: changed branches never taken (line->target; negative = function exit): {mbr}")

    # 2. new tests that add no coverage of their own
    from coverage import CoverageData
    d = CoverageData(basename=str(OUT / ".coverage"))
    d.read()
    per = {}  # test id (params stripped) -> set((file, line)) in src code
    for mf in d.measured_files():
        if is_test_file(mf) or not under(mf, srcs):
            continue
        for ln, ctxs in d.contexts_by_lineno(mf).items():
            for c in ctxs:
                if c.endswith("|run"):
                    per.setdefault(re.sub(r"\[.*$", "", c[:-4]), set()).add((mf, ln))
    warn = []
    for p, lines in sorted(chg.items()):
        if not is_test_file(p) or not Path(p).exists():
            continue
        src = Path(p).read_text().splitlines()
        cls = None
        for i, t in enumerate(src, 1):
            m = re.match(r"class (\w+)", t)
            if m:
                cls = m[1]
            m = re.match(r"(\s*)(?:async\s+)?def (test\w*)\s*\(", t)
            if not m or i not in lines:
                continue
            if "test-reason:" in t or (i > 1 and "test-reason:" in src[i - 2]):
                continue
            tid = f"{p}::{cls + '::' if m[1] and cls else ''}{m[2]}"
            mine = per.get(tid, set())
            others = set().union(*(v for k, v in per.items() if k != tid))
            if not mine - others:
                warn.append(f"{tid}: covers no line other tests miss -> delete it, fold it into an existing "
                            f"test, or add '# test-reason: <what it catches that others do not>'")

    for b in bad:
        print("FAIL", b)
    for w in warn:
        print("WARN", w)
    if not bad and not warn:
        print(f"check_tests: OK (every changed --src line and branch is tested; no redundant new tests; base {a.base})")
    return 1 if bad or (warn and a.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
