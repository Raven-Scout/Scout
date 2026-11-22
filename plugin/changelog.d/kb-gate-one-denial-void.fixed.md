- **One denied tool call no longer ends a run, scheduled or eval: the rule is now in the runner prompt**
  (`templates/run-scout.sh.tmpl`, `run-dreaming.sh.tmpl`, `run-research.sh.tmpl`). The v0.18.21 KB gate's
  session stopped after 19 s because one compound Bash (a path outside the vault) was denied, which it
  read as "Bash is denied in this session". SKILL.md's Step 0 already said a denial covers one call, but
  it sits past what one Read of SKILL.md returns, so the session never saw it. Every runner prompt now
  says it, before the first tool call, including "no command substitution, variables or loops" (the
  v0.18.19 cause). Vaults get it with `/scout-update`.
- **The KB-writing gate reports a run that stopped after a refused call as `void`, not `fail`**
  (`engine/tests/eval/harness/kb_gate.py`). A session that had at least one call refused (by Claude
  Code's permissions or a Scout gate hook) and then ended with no commit, no file written and the
  project's pages unchanged tested no KB writing: `eval.sh kb-gate` now says `"verdict": "void"`
  (exit 3, rerun it), with a `void` block listing each refused call, what refused it and its command;
  the checks are still reported. A run that did nothing with no refusal stays a `fail`, so a change
  that makes runs skip KB writing still blocks the release. The transcript metrics mark each call
  Claude Code refused (`ToolCall.denied`).
