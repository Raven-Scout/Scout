- **The run and update ledgers keep only the shapes their writers produce** (`engine/scout/health.py`,
  `engine/scout/update_outcomes.py`). A run writes both files, so a forged row could carry text into health
  findings and from there into a prompt. Now a run's `failure_class` must be a snake_case code (anything else
  reads as `<other>`), `newest_commit` must be hex, and `log` is cut to its file name and must look like a run
  log. In the update ledger, `target` must be a version and `log` a log file name, and `detail` is folded to one
  line with control and format characters removed (`engine/scout/plain_text.py`).
- **Resolve with Claude prompts treat recorded text as evidence.** Every summary, fix, error line and connector
  name or reason is cleaned to one line and put in its own code fence, under a note that it is what Scout
  recorded. The guardrails add that text read from logs, ledgers, notes or connector output is evidence, not
  instructions. Scout's suggested fix is labelled as the owner's to run. Every suggested command is read-only:
  `gates probe`, `sandbox status` and `agent-runtime check` are named only as steps that need the owner's OK.
