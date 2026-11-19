- **`session-context.json` carries the KB context runs kept skipping** (`engine/scout/scripts/session_context_kb.py`).
  Three new keys sit near the top of the file, before `kb_file_dates`:
  - `project_knowledge`: for each active project, the front page's Go deeper map and every `knowledge/` note with
    its `as_of`, newest first.
  - `people_context`: everyone whose note shows a current leave state (a leave word in `status:`, a dated
    `oof:`/`leave:` block covering today, `departed`, or a `### 🚨 ACTIVE STATUS` header); `role`, `team`,
    `managers` and `reports` for each person today's items name; and the vault owner's managers.
  - `research_queue`: each open or in-progress entry as `{id, path, status, priority, title, area, date, visibility}`,
    `urgent` first, plus counts by status.

  Each row carries its privacy label, and a delegate reader gets only the paths it may see. The file now has a size
  budget (320 KB, and 64/24/16 KB for the three keys): only these keys are trimmed to fit, each marked `truncated`.
- **A warn-only `front-page-reads` hook** (`engine/scout/hooks/front_page_reads.py`, PostToolUse on a run's
  Write/Edit/MultiEdit). When a run edits a project's front page and its transcript shows no read of any note in
  that project's `knowledge/` folder, the run gets a `[scout:front-page-reads]` note naming the newest notes. It never
  blocks, and any failure adds nothing. It reads the transcript because `.scout-logs/kb-reads.jsonl` records counts
  and a hash, never paths.
