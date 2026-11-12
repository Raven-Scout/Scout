---
status: accepted
---

# Per-tool-call accounting replays the transcript at Stop; per-call hooks exit in shell

A PostToolUse hook that started Python on every tool call cost 200-500 ms each, 20-50 seconds of a 100-call run, the largest single cost of a run. Connector-call accounting instead runs once, in a Stop hook that replays the session transcript and writes the same rows. The trade-off is that per-call data exists only after the session ends. Per-call hooks are still used where they must act during the session (labelling knowledge-base reads, recording shared reads), but each must decide in shell whether it applies before starting Python. Landed in [#92](https://github.com/Raven-Scout/Scout/pull/92) (2026-06-01, issue [#72](https://github.com/Raven-Scout/Scout/issues/72)); the token-accounting Stop hook follows the reasoning in [the usage and connector-health spec](../superpowers/specs/2026-04-22-usage-and-connector-health-design.md).
