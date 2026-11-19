- **`session-context.json` carries the KB context runs kept skipping** (`engine/scout/scripts/session_context_kb.py`).
  Three new keys sit near the top of the file, before `kb_file_dates`:
  - `project_knowledge`: `{projects, total, truncated}`, with one row per active project: the front page's Go deeper
    map and every `knowledge/` note with its `as_of`, newest first. A project counts as inactive only when its
    `status:` or `priority:` starts with done, paused, archived or ⚪.
  - `people_context`: `on_leave` lists everyone whose note shows a current leave state:
    - a leave word in `status:` (but not one starting "back" or "active");
    - an `oof:`, `leave:`, `ooo:`, `out_of_office:` or `on_leave:` frontmatter entry in force today (dated or not,
      and not ended);
    - a `status:` that starts with departed, left or offboarded;
    - a heading with 🚨 or ACTIVE STATUS anywhere in the note.

    It also holds `role`, `team`, `managers` and `reports` (each `{name, path, visibility}`) for every person
    today's items name, and the vault owner's managers. Only a listed leave state is meant to be trusted. A person
    not listed still gets the Leave-State gate's lookup.
  - `research_queue`: each entry that is open, in progress or has no status, as
    `{id, path, status, priority, title, area, date, visibility}`, `urgent` first, plus counts by status. Each
    entry's whole frontmatter block is parsed, however long.

  Each row carries its privacy label, and a delegate reader gets only the paths it may see. The file now has a size
  budget: 320 KB in total, and 64, 24 and 16 KB for the three keys. Only these keys are trimmed to fit. Each key
  that loses anything is marked `truncated`, including when whole projects are dropped; `total` still counts them.
- **A warn-only `front-page-reads` hook** (`engine/scout/hooks/front_page_reads.py`, PostToolUse on a run's
  Write/Edit/MultiEdit). When a run edits a project's front page and neither its transcript nor a subagent's shows a
  read of a note in that project's `knowledge/` folder, the run gets a `[scout:front-page-reads]` note.
  - It names the newest notes by `as_of`, marks private ones, and names only notes the run's reader may see. It
    says nothing at all about a page that reader can't see.
  - It never blocks, stops reading at the first match or 64 MB of transcript, and adds nothing on any failure.
  - It reads the transcript because `.scout-logs/kb-reads.jsonl` records counts and a hash, never paths.
