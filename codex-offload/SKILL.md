---
name: codex-offload
description: "Superseded by the cli-subagents skill. Use cli-subagents for offloading work to the user's Codex CLI (engine codex) or the school Claude Code CLI (engine claude): general task mode and the guarded code loop. Load cli-subagents when the user says offload/delegate to Codex, or for any large self-contained code or text-editing task."
---

# codex-offload (superseded)

All content moved to the `cli-subagents` skill (engine `codex` keeps the previous behavior: guarded loop, usage gate, ladder `default,gpt-6-astra:xhigh,gpt-6-astra:max`, ledger, canary). Load it with `skill({skill: "cli-subagents"})`.

Scripts now live in `~/cli-subagents/` on the host (`agent_loop.py --engine codex` replaces `codex_loop.py`; `agent_canary.py --engine codex` replaces `codex_canary.py`; `agent_ledger.py` replaces `codex_ledger.py`). The old deployment in `~/codex-offload/` and its ledger were left in place; the ledger was copied into `~/cli-subagents/ledger.jsonl`.
