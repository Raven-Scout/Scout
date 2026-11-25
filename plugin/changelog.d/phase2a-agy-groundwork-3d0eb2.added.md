- **The groundwork for Antigravity CLI runs** (runtime-agnostic Phase 2A; `engine/scout/agent_runtimes/antigravity.py`,
  `scripts/spikes/agy-launchd-login.sh`). An `antigravity` adapter skeleton gives where agy lives, its version and
  its run-sandbox paths. Its data folder is the only thing a run may write under `~/.gemini`, never its hook config,
  and inside that folder never `builtin/`: agy loads the skills and plugins there into every later session
  (`engine/scout/sandbox/profile.py`, `AGENT_DATA_REDENY`). The adapter isn't registered yet, so no slot can choose
  it. Kernel tests pin, under the run profile, agy's data folder as writable and its gate config, `builtin/` and
  binary as not. A spike script prints a one-shot launchd job, for the owner
  to load by hand, that checks agy's sign-in from launchd.
