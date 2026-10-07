#!/usr/bin/env python3
"""codex_mutate.py TREE --target FILE [--target FILE ...] --test-cmd CMD [--max-mutants 60] [--timeout 120] [--seed 0] [--min-score 0.8]
Stdlib mutation tester for Python source. Mutates the --target files one change at a time (comparison, arithmetic, boolean,
constant, return value, if-condition), runs CMD in a temp copy of TREE, and reports which mutants the tests kill (CMD exits non-zero or
times out). Tests that cannot tell mutated code from the original are vacuous or weak.
Precondition: CMD must pass on the unmutated tree. Output: one JSON object. Exit 0 if score >= --min-score, 1 if below, 2 if the
baseline is red or there is nothing to mutate. Survivors can be equivalent mutants, so a human or Claude reads the survivor list."""
import argparse, ast, copy, json, os, random, shutil, subprocess, sys, tempfile

CMP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}
BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult, ast.FloorDiv: ast.Mult, ast.Mod: ast.Mult}
IGNORE = shutil.ignore_patterns(".git", ".codex-offload", "__pycache__", ".pytest_cache")

def _is_docstring(node, parent):
    return isinstance(parent, ast.Expr) and isinstance(node, ast.Constant) and isinstance(node.value, str)

def candidates(tree):
    """List of (node_index, description, lineno). node_index counts ast.walk order so the same node is found after deepcopy."""
    out = []; skip = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__":
            for c in ast.walk(n): skip.add(id(c))
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            skip.add(id(n.value))
        if isinstance(n, ast.Assert): 
            for c in ast.walk(n): skip.add(id(c))
    for i, n in enumerate(ast.walk(tree)):
        if id(n) in skip or not hasattr(n, "lineno"): continue
        if isinstance(n, ast.Compare):
            for j, op in enumerate(n.ops):
                if type(op) in CMP: out.append((i, ("cmp", j), f"{type(op).__name__} -> {CMP[type(op)].__name__}", n.lineno))
        elif isinstance(n, ast.BinOp) and type(n.op) in BIN: out.append((i, ("bin",), f"{type(n.op).__name__} -> {BIN[type(n.op)].__name__}", n.lineno))
        elif isinstance(n, ast.AugAssign) and type(n.op) in (ast.Add, ast.Sub): out.append((i, ("aug",), f"{type(n.op).__name__}= swapped", n.lineno))
        elif isinstance(n, ast.BoolOp): out.append((i, ("bool",), f"{type(n.op).__name__} -> {'Or' if isinstance(n.op, ast.And) else 'And'}", n.lineno))
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not): out.append((i, ("not",), "not removed", n.lineno))
        elif isinstance(n, ast.Constant) and isinstance(n.value, bool): out.append((i, ("bconst",), f"{n.value} -> {not n.value}", n.lineno))
        elif isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool): out.append((i, ("num",), f"{n.value} -> {n.value + 1}", n.lineno))
        elif isinstance(n, ast.Return) and n.value is not None and not (isinstance(n.value, ast.Constant) and n.value.value is None): out.append((i, ("ret",), "return value -> None", n.lineno))
        elif isinstance(n, ast.If): out.append((i, ("if",), "if condition negated", n.lineno))
    return out

def apply(tree, idx, kind):
    t = copy.deepcopy(tree)
    n = list(ast.walk(t))[idx]
    k = kind[0]
    if k == "cmp": n.ops[kind[1]] = CMP[type(n.ops[kind[1]])]()
    elif k == "bin": n.op = BIN[type(n.op)]()
    elif k == "aug": n.op = ast.Sub() if isinstance(n.op, ast.Add) else ast.Add()
    elif k == "bool": n.op = ast.Or() if isinstance(n.op, ast.And) else ast.And()
    elif k == "bconst": n.value = not n.value
    elif k == "num": n.value = n.value + 1
    elif k == "ret": n.value = ast.Constant(value=None)
    elif k == "if": n.test = ast.UnaryOp(op=ast.Not(), operand=n.test)
    elif k == "not":
        # replace the UnaryOp node by its operand: rewrite the parent's field that holds it
        for p in ast.walk(t):
            for fld, val in ast.iter_fields(p):
                if val is n: setattr(p, fld, n.operand)
                elif isinstance(val, list):
                    for q, item in enumerate(val):
                        if item is n: val[q] = n.operand
    ast.fix_missing_locations(t)
    return ast.unparse(t)

def run_cmd(cmd, cwd, timeout):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for dp, dn, fn in os.walk(cwd):
        for d in list(dn):
            if d == "__pycache__": shutil.rmtree(os.path.join(dp, d), ignore_errors=True); dn.remove(d)
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-600:]
    except subprocess.TimeoutExpired:
        return 124, "timeout"

def mutate(tree_dir, targets, test_cmd, max_mutants=60, timeout=120, seed=0):
    tmp = tempfile.mkdtemp(prefix="mut_"); work = os.path.join(tmp, "t"); shutil.copytree(tree_dir, work, symlinks=True, ignore=IGNORE)
    try:
        rc, out = run_cmd(test_cmd, work, timeout)
        if rc != 0: return {"status": "baseline_red", "detail": out}
        cands = []
        for tg in targets:
            path = os.path.join(work, tg)
            if not os.path.exists(path): return {"status": "bad_target", "detail": tg}
            src = open(path).read(); tree = ast.parse(src)
            for idx, kind, desc, line in candidates(tree): cands.append((tg, tree, idx, kind, desc, line, src))
        if not cands: return {"status": "nothing_to_mutate", "detail": "no mutable constructs in targets"}
        rnd = random.Random(seed); n_cand = len(cands)
        if n_cand > max_mutants: cands = rnd.sample(cands, max_mutants)
        killed = 0; survivors = []; timeouts = 0
        for tg, tree, idx, kind, desc, line, src in sorted(cands, key=lambda c: (c[0], c[5])):
            path = os.path.join(work, tg)
            try: mutated = apply(tree, idx, kind)
            except Exception: continue
            open(path, "w").write(mutated)
            rc, out = run_cmd(test_cmd, work, timeout)
            open(path, "w").write(src)
            if rc != 0: killed += 1; timeouts += rc == 124
            else: survivors.append({"file": tg, "line": line, "change": desc})
        n = killed + len(survivors)
        return {"status": "ok", "candidates": n_cand, "run": n, "killed": killed, "timeouts": timeouts, "survivors": survivors, "score": round(killed / n, 3) if n else None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("tree"); ap.add_argument("--target", action="append", required=True); ap.add_argument("--test-cmd", required=True)
    ap.add_argument("--max-mutants", type=int, default=60); ap.add_argument("--timeout", type=int, default=120); ap.add_argument("--seed", type=int, default=0); ap.add_argument("--min-score", type=float, default=0.8)
    a = ap.parse_args()
    r = mutate(os.path.abspath(a.tree), a.target, a.test_cmd, a.max_mutants, a.timeout, a.seed)
    print(json.dumps(r))
    if r["status"] != "ok": return 2
    return 0 if (r["score"] is not None and r["score"] >= a.min_score) else 1

if __name__ == "__main__":
    sys.exit(main())
