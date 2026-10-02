"""Tests for the standing-instructions sidecar (kernel.py). Each test reproduces a known failure pattern.
Known-bad inputs must raise; known-good inputs must pass.

Run:  KERNEL=skill_build/kernel.py NB_KERNEL=skill_build/nb_kernel.py python standing-instructions_tests.py
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess
import threading
import traceback

KERNEL = os.environ.get("KERNEL", "skill_build/kernel.py")
NB_KERNEL = os.environ.get("NB_KERNEL", "skill_build/nb_kernel.py")
exec(open(KERNEL).read(), globals())

RESULTS = []


def t(name, incidents):
    def deco(fn):
        try:
            fn()
            RESULTS.append({"test": name, "incidents": incidents, "passed": True, "detail": ""})
        except Exception:
            RESULTS.append({"test": name, "incidents": incidents, "passed": False, "detail": traceback.format_exc()[-600:]})
        return fn
    return deco


def raises(kind, fn, contains=None):
    """Assert fn raises the SI exception `kind` (and its message contains `contains`)."""
    try:
        fn()
    except Exception as e:
        want = si_error(kind)
        assert isinstance(e, want), f"expected {want.__name__}, got {type(e).__name__}: {e}"
        if contains:
            assert contains in str(e), f"message lacks {contains!r}: {e}"
        return e
    raise AssertionError(f"expected SI{kind}Error but nothing was raised")


def mkrepo(commit=True):
    d = tempfile.mkdtemp(prefix="si_test_")
    subprocess.run(["git", "init", "-q", d], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@example.org"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    if commit:
        open(os.path.join(d, "outline.md"), "w").write("# Outline\n")
        open(os.path.join(d, "README.md"), "w").write("# Repo\n")
        subprocess.run(["git", "-C", d, "add", "-A"], check=True)
        subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"], check=True)
    return d


def w(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)
    return p


D = "2026-09-20"


def q(s):
    return f'"{s}" (user, {D})'


# ---- shared project with the user's real standing rules -------------------------------------------------
ROOT = mkrepo()
si_init(ROOT, "demo")
R = {}
R["human"] = si_add("Report foundation models on human datasets only", "data", q("I have said many times, exclude non-human from foundation models"),
                    ROOT, check={"forbid_literal": ["dataset_E"]}, keywords=["foundation"], files=["*.md", "*.csv"])["id"]
R["itemize"] = si_add("No itemize lists; explain the statistical point of view in prose", "format", q("don't use itemize, explain the statistical point of view in prose"),
                      ROOT, check={"forbid_literal": ["\\begin{itemize}"]}, files=["*.tex"])["id"]
R["png"] = si_add("Figures are PNG, not PDF", "format", q("PNG not PDF"), ROOT, check={"forbid": [r"\.pdf\b"], "ignore_case": True},
                  keywords=["figure"], files=["*.tex", "*.md"])["id"]
R["figref"] = si_add("Reference figures by filename in a figures/ folder, never by artifact id", "format", q("store figures in a figures folder and reference them by filename"),
                     ROOT, check={"forbid": [r"artifact:"], "require": [r"\\includegraphics(\[[^\]]*\])?\{figures/[^}]+\.png\}"]}, files=["*.tex"])["id"]
R["mdfig"] = si_add("The markdown outline embeds the figures it describes", "format", q("why are there no figures in the MD file? There is just text"),
                    ROOT, check={"require": [r"!\[[^\]]*\]\([^)]+\.png\)"]}, files=["outline*.md"], keywords=["outline"])["id"]
R["narr"] = si_add("The outline contains no analysis or narrative the user did not dictate", "writing", q("There shouldn't be any analysis or narrative in the outline because I did not dictate any"),
                   ROOT, keywords=["outline"])["id"]
R["excl"] = si_add("Keep these papers out of the review", "data", q("Ensure that TopoLa is not in the review"), ROOT,
                   check={"forbid_literal": ["TopoLa", "scRegulate"]}, keywords=["review"], files=["review*.md"])["id"]
R["scib"] = si_add("Use the published scIB scoring including kBET", "method", q("I want to use scientific standard scIB scores"), ROOT,
                   check={"require_literal": ["kBET"]}, keywords=["scib", "batch"], files=["metrics*.md"])["id"]
R["pairs"] = si_add("Dataset design has three perturbation pairs: IFN, LPS, T-cell", "method", q("yes, use the 3-pair design IFN/LPS/T-cell"), ROOT,
                    check={"require_literal": ["IFN", "LPS", "T-cell"]}, keywords=["design"], files=["design*.md"])["id"]


@t("ledger round-trips; ids sequential; init does not overwrite", [])
def _():
    led = si_load(ROOT)
    assert [r["id"] for r in led["rules"]] == [f"SI-{i:02d}" for i in range(1, 10)], led["rules"]
    assert si_init(ROOT)["created"] is False
    assert len(open(os.path.join(ROOT, "CONSTRAINTS.md")).read()) > 500


@t("rule without traceable source is refused (invented framing)", [])
def _():
    raises("Error", lambda: si_add("Central question is predictive power", "writing", "the agent thinks so", ROOT), "source must quote")
    raises("Error", lambda: si_add("Central question is predictive power", "writing", '"quote but no date"', ROOT), "source must quote")


@t("known-bad: non-human dataset in foundation-model table fails; human-only passes", [])
def _():
    bad = "| model | dataset |\n|---|---|\n| scGPT | dataset_A |\n| scGPT | dataset_E |\n"
    e = raises("Violation", lambda: si_check_output(bad, [si_load(ROOT)["rules"][0]]), R["human"])
    assert e.result["violations"][0]["hits"][0]["line"] == 4
    good = si_check_output("| scGPT | dataset_A |\n", [si_load(ROOT)["rules"][0]])
    assert good["ok"] and good["passed"] == [R["human"]]


@t("known-bad: \\begin{itemize} fails in .tex, prose passes, same text in .md is skipped not passed", [])
def _():
    rules = si_applicable("rewrite the statistics section of the paper in latex", root=ROOT)["matched"]
    assert R["itemize"] in [r["id"] for r in rules]
    d = tempfile.mkdtemp()
    bad = w(d, "stats.tex", "\\section{Stats}\n\\begin{itemize}\n\\item a\n\\end{itemize}\n")
    good = w(d, "stats_ok.tex", "\\section{Stats}\nThe test statistic is compared with a null.\n")
    md = w(d, "stats.md", "\\begin{itemize}\n")
    only = [r for r in rules if r["id"] == R["itemize"]]
    raises("Violation", lambda: si_check_output(bad, only), "itemize")
    assert si_check_output(good, only)["ok"]
    r = si_check_output(md, only)
    assert r["skipped"] and not r["checked"] and not r["passed"], r


@t("known-bad: includegraphics by artifact id fails; filename in figures/ passes", [])
def _():
    rules = [r for r in si_load(ROOT)["rules"] if r["id"] == R["figref"]]
    d = tempfile.mkdtemp()
    bad = w(d, "paper.tex", "\\includegraphics[width=\\textwidth]{{{artifact:art_7372ae59-e330-4187-bdf7-06c43d0dbcc3}}}\n")
    good = w(d, "paper_ok.tex", "\\includegraphics[width=\\textwidth]{figures/fig1.png}\n")
    e = raises("Violation", lambda: si_check_output(bad, rules))
    kinds = {v["kind"] for v in e.result["violations"]}
    assert kinds == {"forbidden", "missing"}, kinds
    assert si_check_output(good, rules)["ok"]


@t("known-bad: PDF figure reference fails, case-insensitive; PNG passes", [])
def _():
    rules = [r for r in si_load(ROOT)["rules"] if r["id"] == R["png"]]
    raises("Violation", lambda: si_check_output("see Fig1.PDF for details\n", rules), "pdf")
    assert si_check_output("see fig1.png for details\n", rules)["ok"]


@t("known-bad: markdown outline with text only fails; with embedded PNG passes", [])
def _():
    rules = [r for r in si_load(ROOT)["rules"] if r["id"] == R["mdfig"]]
    d = tempfile.mkdtemp()
    bad = w(d, "outline_v2.md", "# Outline\n- metric A: description\n")
    good = w(d, "outline_v3.md", "# Outline\n![metric A](figures/a.png)\n")
    raises("Violation", lambda: si_check_output(bad, rules), "missing")
    assert si_check_output(good, rules)["ok"]


@t("narrative rule has no pattern: surfaced as manual, never reported as passed", [])
def _():
    rules = [r for r in si_load(ROOT)["rules"] if r["id"] == R["narr"]]
    r = si_check_output("# Outline\nThis suggests metrics predict capability.\n", rules)
    assert r["ok"] and r["needs_manual_review"] and r["manual"][0]["id"] == R["narr"] and r["passed"] == []


@t("known-bad: excluded paper reappears in review; clean review passes", [])
def _():
    rules = [r for r in si_load(ROOT)["rules"] if r["id"] == R["excl"]]
    d = tempfile.mkdtemp()
    raises("Violation", lambda: si_check_output(w(d, "review_table.md", "| scRegulate | 2024 |\n"), rules), "scRegulate")
    assert si_check_output(w(d, "review_table2.md", "| scVI | 2018 |\n"), rules)["ok"]


@t("known-bad: substituted 'fairer' scIB criterion without kBET fails", [])
def _():
    rules = [r for r in si_load(ROOT)["rules"] if r["id"] == R["scib"]]
    d = tempfile.mkdtemp()
    raises("Violation", lambda: si_check_output(w(d, "metrics_batch.md", "batch score = ASW + iLISI\n"), rules), "kBET")
    assert si_check_output(w(d, "metrics_batch2.md", "batch score = ASW + iLISI + kBET\n"), rules)["ok"]


@t("known-bad: design doc silently drops confirmed LPS pair; 3-pair doc passes", [])
def _():
    rules = [r for r in si_load(ROOT)["rules"] if r["id"] == R["pairs"]]
    d = tempfile.mkdtemp()
    bad = w(d, "design.md", "Pairs: IFN, T-cell (the second stimulus was interferon-dominated, redundant with IFN)\n")
    e = raises("Violation", lambda: si_check_output(bad, rules), "LPS")
    assert [v["pattern"] for v in e.result["violations"]] == ["LPS"], e.result["violations"]
    assert si_check_output(w(d, "design2.md", "Pairs: IFN, LPS, T-cell\n"), rules)["ok"]


@t("near-duplicate rule refused; force overrides", [])
def _():
    raises("Duplicate", lambda: si_add("Report foundation models on human datasets only.", "data", q("again: human only"), ROOT), R["human"])
    n = si_add("Report foundation models on human datasets only.", "data", q("again: human only"), ROOT, force=True)["id"]
    si_retire(n, "duplicate entered to test force override", ROOT)


@t("retire keeps the entry; retired rules are not applicable and cannot be checked; double retire raises", [])
def _():
    r = si_add("Slides use a 16:9 layout", "format", q("use 16:9"), ROOT)["id"]
    si_retire(r, 'user lifted it: "any ratio is fine" (2026-09-21)', ROOT)
    led = si_load(ROOT)
    assert r in [x["id"] for x in led["retired"]] and r not in [x["id"] for x in led["active"]]
    raises("Ledger", lambda: si_retire(r, "retire a second time please", ROOT), "already retired")
    raises("Error", lambda: si_check_output("x\n", [led["retired"][-1]]), "retired")
    n = si_add("Slides use a 4:3 layout", "format", q("use 4:3"), ROOT)["id"]
    assert int(n[3:]) > int(r[3:])
    si_retire(n, "test cleanup retire", ROOT)


@t("si_applicable returns the rules for a foundation-model table task; unrelated task matches none", [])
def _():
    a = si_applicable("build the foundation model results table for the human benchmark", root=ROOT)
    assert R["human"] in [r["id"] for r in a["matched"]], a["matched"]
    assert all(r["why"] for r in a["matched"])
    o = si_applicable("rotate and compress old log archives", root=ROOT)
    assert o["matched"] == [] and len(o["unmatched"]) == o["ledger_active"], o
    raises("Error", lambda: si_applicable("   ", root=ROOT), "task_text is empty")
    raises("Error", lambda: si_applicable("outline", scopes=["colour"], root=ROOT), "unknown scopes")


@t("outline task pulls in narrative + markdown-figure rules", [])
def _():
    ids = [r["id"] for r in si_applicable("draft the markdown outline for the paper", root=ROOT)["matched"]]
    assert R["narr"] in ids and R["mdfig"] in ids


@t("empty/ missing inputs fail loudly", [])
def _():
    raises("Error", lambda: si_check_output("text\n", []), "no rules supplied")
    raises("Error", lambda: si_check_output("/nonexistent/paper.tex", si_load(ROOT)["active"]), "does not exist")
    raises("Error", lambda: si_add("Bad regex rule here", "data", q("x y z w"), ROOT, check={"forbid": ["(unclosed"]}), "valid regex")
    raises("Error", lambda: si_add("Bad scope rule here", "colour", q("x y z w"), ROOT), "scope")
    raises("Error", lambda: si_add("Empty pattern rule here", "data", q("x y z w"), ROOT, check={"forbid": [""]}), "empty pattern")
    empty = mkrepo()
    raises("Ledger", lambda: si_load(empty), "si_init")


@t("malformed ledger is rejected, not skipped", [])
def _():
    d = mkrepo()
    open(os.path.join(d, "CONSTRAINTS.md"), "w").write("# x\n\n## SI-01 - active - data\n- rule: a rule here\n")
    raises("Ledger", lambda: si_load(d), "malformed")
    open(os.path.join(d, "CONSTRAINTS.md"), "w").write("# x\n\n## SI-01 \u00b7 active \u00b7 colour\n- rule: a rule here\n")
    raises("Ledger", lambda: si_load(d), "scope")
    open(os.path.join(d, "CONSTRAINTS.md"), "w").write("# x\n\n## SI-01 \u00b7 active \u00b7 data\n- rule: a rule here\n- source: s\n- added: 2026-01-01\n- check: {oops\n")
    raises("Ledger", lambda: si_load(d), "JSON")


@t("known-bad: user's questions skipped before executing plan; answered passes; dangling pointer raises", [])
def _():
    msgs = ["I am unconvinced about point 1. How would the zero-signal test help for low signal data?\nIs dataset_E human?",
            "Answer my questions in the previous message before we execute this plan"]
    e = raises("Unanswered", lambda: si_open_questions(msgs), "not yet answered")
    assert len(e.result["unanswered"]) == 2, e.result
    ok = si_open_questions(msgs, answered=["The zero-signal test helps for low signal data because it fixes the null level",
                                           "dataset_E is a mouse dataset, not human"])
    assert ok["ok"] and ok["n_questions"] == 2
    one = si_open_questions(msgs, answered=["The zero-signal test helps for low signal data because it fixes the null level"], strict=False)
    assert one["unanswered"] == ["Is dataset_E human?"]
    raises("Error", lambda: si_open_questions([msgs[1]]), "earlier message")
    assert si_open_questions(["Don't use itemize. Render the figure as PNG."])["n_questions"] == 0
    r = si_open_questions(["I don't understand how the regularizer makes batch integration worse. Show me the results"], strict=False)
    assert r["n_questions"] == 1 and not r["ok"]


@t("known-bad: corrected claim left in paper (wrapped across lines), README counts, memory row; fixed state is clean", [])
def _():
    d = mkrepo()
    w(d, "paper.tex", "We find nearly independent\ndirections in the factor space.\nIntro: nearly independent directions.\n\\caption{Nearly  independent directions}\n")
    w(d, "README.md", "| files/ | 99 PDFs + 11 .txt |\n")
    w(d, "notebook/2026-09-01.md", "old history: nearly independent directions\n")
    w(d, "CONSTRAINTS.md", "nearly independent directions\n")
    e = raises("Stale", lambda: si_propagate("nearly independent directions", d), "3 stale")
    assert [h["line"] for h in e.result["hits"]] == [1, 3, 4], e.result["hits"]
    assert all("notebook" not in h["path"] and "CONSTRAINTS" not in h["path"] for h in e.result["hits"])
    raises("Stale", lambda: si_propagate(r"99 PDFs", d, regex=True), "1 stale")
    mem = {"memory:mem_x": "Durable path is the parent directory local_run"}
    raises("Stale", lambda: si_propagate("parent directory local_run", d, extra_texts=mem), "memory:mem_x")
    w(d, "paper.tex", "Directions are not independent beyond chance.\n")
    w(d, "README.md", "| files/ | 135 PDFs |\n")
    assert si_propagate("nearly independent directions", d)["clean"]


@t("propagate refuses vacuous scans and bad inputs", [])
def _():
    d = tempfile.mkdtemp()
    raises("Error", lambda: si_propagate("stale claim", d), "scanned nothing")
    raises("Error", lambda: si_propagate("stale claim", "/no/such/dir"), "does not exist")
    raises("Error", lambda: si_propagate("ab", d), "at least 3")


@t("known-bad: files changed beyond the brief (README, new figures); in-scope-only passes", [])
def _():
    d = mkrepo()
    base = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    w(d, "outline.md", "# Outline\nremoved paper\n")
    ok = si_scope_check(["outline.md"], d, base=base)
    assert ok["ok"] and ok["in_scope"] == ["outline.md"]
    w(d, "README.md", "# Repo\nextra bibliometrics\n")
    w(d, "figures/new.png", "x")
    e = raises("Scope", lambda: si_scope_check(["outline.md"], d, base=base), "README.md")
    assert e.result["out_of_scope"] == ["README.md", "figures/new.png"], e.result
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "scope creep"], check=True)
    raises("Scope", lambda: si_scope_check(["outline.md"], d, base=base), "figures/new.png")
    assert si_scope_check(["outline.md", "README.md", "figures/"], d, base=base)["ok"]
    raises("Error", lambda: si_scope_check([], d), "allowed is empty")
    raises("Ledger", lambda: si_scope_check(["x"], tempfile.mkdtemp() + "/nope"), "not a directory")
    nogit = tempfile.mkdtemp()
    raises("Scope", lambda: si_scope_check(["x"], nogit), "git diff")


@t("notebook mirroring: NB source verified; unmirrored user DECISION flagged; later CORRECTION fails sync", [])
def _():
    d = mkrepo()
    ns = {}
    exec(open(NB_KERNEL).read(), ns)
    ns["nb_init"](d, "demo", "UTC")
    si_init(d)
    e1 = ns["nb_entry"](d, "DECISION", "Use category-first layout", {"decision": "option b: category-first scIB layout", "alternatives": "option a",
                                                                       "rationale": "user override", "decided by": "user"}, commit=False)["id"]
    e2 = ns["nb_entry"](d, "DECISION", "Pick seed 7", {"decision": "seed 7", "alternatives": "seed 1", "rationale": "arbitrary", "decided by": "agent"}, commit=False)["id"]
    raises("Notebook", lambda: si_add("Layout follows option b", "writing", "NB-20200101-09", d), "not found")
    s = si_sync_notebook(d)
    assert [x["id"] for x in s["unmirrored"]] == [e1], s
    r = si_add("Outline layout is category-first, questions as sections", "writing", e1, d)
    assert r["nb_entry_suggestion"] is None and r["rule"]["notebook"] == [e1]
    assert si_sync_notebook(d)["unmirrored"] == []
    # mirror direction ledger -> notebook: suggestion is usable as nb_entry kwargs
    r2 = si_add("Never reuse probed best configs in the final run", "method", q("we need to recreate all sweeps from base"), d)
    nb = ns["nb_entry"](d, **r2["nb_entry_suggestion"], commit=False)["id"]
    assert si_link(r2["id"], nb, d)["notebook"] == [nb]
    assert si_sync_notebook(d)["ok"]
    raises("Notebook", lambda: si_link(r2["id"], "NB-20200101-09", d), "not an entry")
    ns["nb_entry"](d, "CORRECTION", "layout is option a after all", {"corrects": e1, "was": "option b", "correct": "option a"}, commit=False)
    raises("Notebook", lambda: si_sync_notebook(d), "corrected")
    assert ns["nb_check"](d)["ok"]


@t("concurrent agents get unique ids and no lost writes", [])
def _():
    d = mkrepo()
    si_init(d)
    errs = []

    def work(i):
        try:
            si_add(f"Concurrency rule number{i} about topic{i}x{i}", "process", q(f"concurrent quote number {i}"), d)
        except Exception as e:
            errs.append(repr(e))

    th = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [x.start() for x in th]
    [x.join() for x in th]
    ids = [r["id"] for r in si_load(d)["rules"]]
    assert not errs and len(ids) == 8 and len(set(ids)) == 8, (errs, ids)


if __name__ == "__main__":
    p = sum(r["passed"] for r in RESULTS)
    f = len(RESULTS) - p
    json.dump({"passed": p, "failed": f, "results": RESULTS}, open("standing-instructions_test_results.json", "w"), indent=1)
    for r in RESULTS:
        print(("PASS " if r["passed"] else "FAIL ") + r["test"] + ("" if r["passed"] else "\n" + r["detail"]))
    print(f"{p} passed, {f} failed")
    sys.exit(1 if f else 0)
