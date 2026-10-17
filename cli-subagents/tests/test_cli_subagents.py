"""Offline tests: python3 tests/test_cli_subagents.py   (claude is replaced by tests/fake_cli.py; codex is covered by the live canary --full)."""
import json, os, shutil, subprocess, sys, tempfile, unittest
T = os.path.dirname(os.path.abspath(__file__)); S = next(p for p in (os.path.join(os.path.dirname(T), "scripts"), os.path.dirname(T)) if os.path.exists(os.path.join(p, "agent_run.py"))); sys.path.insert(0, S)  # repo layout or flat host deploy
import agent_run, agent_task, agent_canary, agent_ledger
FAKE = os.path.join(T, "fake_cli.py"); os.chmod(FAKE, 0o755)

class T_(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.env = dict(os.environ, CLAUDE_BIN=FAKE, CLI_SUBAGENTS_LEDGER=os.path.join(self.d, "ledger.jsonl"),
                                                     FAKE_ARGV_LOG=os.path.join(self.d, "argv.log"), FAKE_MODE="ok")
        self.proj = os.path.join(self.d, "proj"); os.makedirs(self.proj); open(os.path.join(self.proj, "slug.py"), "w").write("def slugify(s):\n    return s\n")
        open(os.path.join(self.d, "task.md"), "w").write(agent_canary.TASK); open(os.path.join(self.d, "accept.py"), "w").write(agent_canary.ACCEPT)
        open(os.path.join(self.d, "schema.json"), "w").write(json.dumps({"type": "string"}))

    def sh(self, script, *args, **env):
        p = subprocess.run([sys.executable, os.path.join(S, script), *args], capture_output=True, text=True, env=dict(self.env, **env)); return p.returncode, p.stdout

    def task(self, *extra, **env):
        out = os.path.join(self.d, "out" + str(len(os.listdir(self.d))))
        rc, so = self.sh("agent_task.py", os.path.join(self.d, "task.md"), "--engine", "claude", "--workdir", self.proj, "--out", out, *extra, **env)
        return rc, json.load(open(os.path.join(out, "result.json"))) if os.path.exists(os.path.join(out, "result.json")) else so

    def test_declaration(self):
        ok = {"result": 1, "deviations": ["none"], "files_changed": []}
        for text, structured, schema, good in [("x\n```json\n" + json.dumps(ok) + "\n```", None, None, True), (json.dumps(ok), None, None, True), ("no block", None, None, False),
                ("```json\n" + json.dumps(dict(ok, deviations=[])) + "\n```", None, None, False), ("```json\n" + json.dumps(dict(ok, files_changed="a")) + "\n```", None, None, False),
                ("```json\n[1]\n```", None, None, False), ("", ok, None, True), ("", ok, {"type": "string"}, False)]:
            with self.subTest(text=text[:20], schema=schema): self.assertEqual(agent_task.parse_declaration(text, structured, schema)[0] is not None, good)

    def test_build_cmd(self):
        c0 = agent_run.build_cmd("codex", "/w", "/o", "read-only", "m", "high", "/s.json", None, is_git=False)
        c1 = agent_run.build_cmd("codex", "/w", "/o", "workspace-write", resume="abc")
        c2 = agent_run.build_cmd("claude", "/w", "/o", "read-only", "opus", "max", "{}", "abc", None, "plan")
        self.assertEqual(c0[1:5], ["exec", "--json", "-o", "/o"]); self.assertIn("--skip-git-repo-check", c0); self.assertEqual(c0[c0.index("-s") + 1], "read-only")
        self.assertEqual(c1[1:3], ["exec", "resume"]); self.assertIn('sandbox_mode="workspace-write"', c1); self.assertEqual(c1[-2:], ["abc", "-"]); self.assertNotIn("-s", c1)
        self.assertEqual(c2[c2.index("--allowedTools") + 1], "Read,Glob,Grep"); self.assertEqual(c2[c2.index("--resume") + 1], "abc"); self.assertEqual(c2[c2.index("--permission-mode") + 1], "plan")
        for c in (c0, c1, c2): self.assertFalse([x for x in c if "danger" in x or "bypass" in x.lower()])
        with self.assertRaises(ValueError): agent_run.run("claude", "p", self.d, self.d, effort="ultra")
        with self.assertRaises(ValueError): agent_run.run("claude", "p", self.d, self.d, perm="bypassPermissions")

    def test_task_flow(self):
        rc, r = self.task("--model", "opus", "--effort", "xhigh")
        self.assertEqual((rc, r["status"], r["session_id"], r["declaration"]["result"]), (0, "DECLARED", "sess-1", "pong"))
        self.assertTrue(r["scratch_copy"] and r["git"]["untracked"] == [] and r["git"]["diff_stat"] and "slug.py" in r["git"]["diff_stat"])  # real tree untouched, diff recorded
        self.assertEqual(open(os.path.join(self.proj, "slug.py")).read(), "def slugify(s):\n    return s\n")
        self.assertRegex(r["usage_report"], r"5h used 2\.0% .*weekly used 5\.0%"); self.assertEqual(r["tokens"]["cached_input_tokens"], 3)
        argv = json.loads(open(self.env["FAKE_ARGV_LOG"]).readline()); self.assertEqual(argv[argv.index("--model") + 1:argv.index("--model") + 2] + [argv[argv.index("--effort") + 1]], ["opus", "xhigh"])
        rc, r2 = self.task("--resume", "sess-9")
        self.assertEqual((rc, r2["session_id"], r2["resumed_from"], r2["scratch_copy"]), (0, "sess-9", "sess-9", False))
        rc, r3 = self.task("--schema", os.path.join(self.d, "schema.json")); self.assertEqual((rc, r3["declaration"]["result"]), (0, "pong"))
        self.assertIn("--json-schema", json.loads(open(self.env["FAKE_ARGV_LOG"]).readlines()[-1]))
        self.assertEqual(self.task("--schema", os.path.join(self.d, "schema.json"), FAKE_RESULT="5")[0], 13)  # result violates schema -> UNDECLARED
        self.assertEqual(agent_run.report_line("claude", None, []), "Claude (school) remaining: not reported by CLI")
        rc, r4 = self.task(FAKE_MODE="nodecl"); self.assertEqual((rc, r4["status"]), (13, "UNDECLARED"))
        rc, r5 = self.task(FAKE_MODE="limit"); self.assertEqual((rc, r5["status"]), (12, "USAGE_LIMIT"))
        self.assertEqual(self.task("--effort", "ultra")[0], 2)
        rows = agent_ledger.read(self.env["CLI_SUBAGENTS_LEDGER"]); self.assertEqual({(x["engine"], x["mode"]) for x in rows}, {("claude", "task")}); self.assertEqual(len(rows), 6)

    def test_loop_claude(self):
        rc, so = self.sh("agent_loop.py", os.path.join(self.d, "task.md"), self.proj, "--engine", "claude", "--accept", f"{sys.executable} {self.d}/accept.py", "--accept-file", f"{self.d}/accept.py")
        self.assertEqual(rc, 0, so)
        loop = json.load(open(next(os.path.join(r, "loop.json") for r in [os.path.join(self.proj, ".cli-subagents", x) for x in os.listdir(os.path.join(self.proj, ".cli-subagents"))] if os.path.isdir(r) and "loop_" in r)))
        self.assertEqual((loop["engine"], loop["ladder"], loop["final"]["status"], loop["final"]["rung"]), ("claude", "opus:xhigh,opus:max", "accepted", "opus:xhigh"))
        self.assertIn("Claude (school) usage from CLI", loop["final"]["usage_report"]["report_line"])
        e = agent_ledger.read(self.env["CLI_SUBAGENTS_LEDGER"])[0]; self.assertEqual((e["engine"], e["mode"], e["loop_mode"]), ("claude", "loop", "implement"))
        argv = json.loads(open(self.env["FAKE_ARGV_LOG"]).readline()); self.assertEqual((argv[argv.index("--model") + 1], argv[argv.index("--effort") + 1]), ("opus", "xhigh"))

    def test_canary_logged_out_and_ledger_migrate(self):
        rc, so = self.sh("agent_canary.py", "--engine", "claude", FAKE_LOGGED_IN="0"); r = json.loads(so)
        self.assertEqual(rc, 1); self.assertFalse(r["checks"]["login"]["ok"]); self.assertIn("LOGGED OUT", r["checks"]["login"]["detail"]); self.assertNotIn("full_known_answer_task", r["checks"])
        cp = os.path.join(self.d, "scripts"); shutil.copytree(S, cp, ignore=shutil.ignore_patterns("__pycache__", "*.lock", "*.jsonl", "MANIFEST*"))  # canary needs a manifest next to the scripts
        open(os.path.join(cp, "MANIFEST.sha256"), "w").write("".join(f"{agent_canary.sha(os.path.join(cp, n))}  {n}\n" for n in agent_canary.SCRIPTS))
        p = subprocess.run([sys.executable, os.path.join(cp, "agent_canary.py"), "--engine", "claude", "--full"], capture_output=True, text=True, env=self.env); r = json.loads(p.stdout)
        self.assertEqual((p.returncode, r["ok"], r["checks"]["full_known_answer_task"]["ok"]), (0, True, True), r)
        old = os.path.join(self.d, "old.jsonl"); open(old, "w").write(json.dumps({"id": "loop_1", "mode": "tests_only", "status": "accepted"}) + "\n")
        self.assertEqual([json.loads(self.sh("agent_ledger.py", "migrate", old)[1])["migrated"] for _ in range(2)], [1, 0])
        row = agent_ledger.read(self.env["CLI_SUBAGENTS_LEDGER"])[0]; self.assertEqual((row["engine"], row["mode"], row["loop_mode"]), ("codex", "loop", "tests_only"))
        self.assertEqual(self.sh("agent_ledger.py", "mark", "loop_1", "--defect")[0], 0); self.assertEqual(json.loads(self.sh("agent_ledger.py", "summary")[1])["escaped_defects"], 1)

if __name__ == "__main__":
    unittest.main()
