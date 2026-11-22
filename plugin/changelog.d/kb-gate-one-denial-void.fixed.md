- **One denied tool call no longer ends a run, scheduled or eval: the rule is now in the runner prompt**
  (`templates/run-scout.sh.tmpl`, `run-dreaming.sh.tmpl`, `run-research.sh.tmpl`). The v0.18.21 KB gate's
  session stopped after 19 s because one compound Bash (a path outside the vault) was denied, which it
  read as "Bash is denied in this session". SKILL.md's Step 0 already said a denial covers one call, but
  it sits past what one Read of SKILL.md returns, so the session never saw it. Every runner prompt now
  says it, before the first tool call. Vaults get it with `/scout-update`.
- **The KB-writing gate reports a run that did nothing as `void`, not `fail`** (`engine/tests/eval/harness/kb_gate.py`).
  A session that ends with no commit, no file written and the project's pages unchanged tested no KB
  writing: `eval.sh kb-gate` now says `"verdict": "void"` (exit 3) with a `void` block listing each
  denied call and its command, and the checks still reported. A `fail` (exit 1) now always means the run
  wrote KB pages badly. The transcript metrics mark each call Claude Code refused (`ToolCall.denied`).
