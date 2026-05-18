# Scout.app — feature roadmap (asks-bigger-than-bugs)

Captured from user conversations 2026-05-17 → 2026-05-18 during the
control-center bug audit. These are *features* (not bug fixes) and need
architectural decisions before any code lands. Don't lose them.

The fix-first principle still holds: data-correctness bugs in the
existing surfaces get cleared before new sidebar items appear. This doc
is the parking lot for things we agreed are worth doing once the
foundations are solid.

---

## F-1. Embedded terminal / inline Claude Code sessions

**Ask:** "Embed ghostty or a terminal of some sort so I can start working
on certain tasks directly from the scout app inside of a claude code
session."

**Why it matters:** Today, "Launch Claude" on an action item opens
Ghostty in a separate window. The context switch breaks the flow of
"see task → pick it up → ship". Putting the terminal inside Scout means
the task list and the agent that's working on the task live in the same
window.

**Approach:**
- Use **SwiftTerm** (https://github.com/migueldeicaza/SwiftTerm) as a
  Swift Package Manager dependency. BSD-licensed, real terminal
  emulator, designed for embedding in AppKit/SwiftUI.
- New SwiftUI view `TerminalPaneView` that hosts SwiftTerm in an
  `NSViewRepresentable`.
- Lifecycle: spawn `claude` (via `claude-code` CLI), feed it the same
  prompt produced by `ClaudeLauncher.prompt(for: task)`.
- When the user closes the pane, write the session id back to the
  action-item-link store (F-4) so the next launch can resume the
  conversation instead of starting cold.

**Open questions:**
- Where does the terminal pane live? Bottom split inside Action Items
  (collapsible drawer)? A dedicated 4th sidebar item? An overlay/sheet
  triggered from any task?
- Do we manage process state across app quits? (Probably no — `claude`
  itself persists session state on disk; we just need to know which
  session id to resume.)
- Color/font: inherit from DS or expose a separate "terminal" theme?
- Behavior on multiple simultaneous terminals (one per task)?

**Dependencies:** F-4 (session↔task linkage) makes "resume" meaningful.
Without F-4, an embedded terminal is just a window inside the window.

---

## F-2. Sessions page

**Ask:** "I want a 'sessions' page […] with a list of all of my recent
claude code sessions so I can revisit them to see what's going on. This
could improve the workflow a lot."

**Why it matters:** All my Claude Code sessions live in
`~/.claude/projects/<encoded-cwd>/<session>.jsonl`. There's no native UI
to browse them — you have to `cd` and `claude --resume`. A Sessions
page gives that browsing surface inside Scout.

**Approach:**
- New top-level sidebar item: **Sessions** (icon: `bubble.left.and.text.bubble.right`)
- Backed by a new `ClaudeCodeSessionsService` that scans every
  `~/.claude/projects/*/*.jsonl` (not just the Scout-tied ones the
  existing `ClaudeSessionService` knows about).
- Row layout: title (or first user message) · project (decoded
  directory) · first timestamp · tool-call count · was-Scout-launched
  badge. Sort by recency. Filter by project + title search.
- Click row → reuse the existing `ToolsTab` / `FilesTab` /
  `SummaryTab` / `LogViewer` infrastructure from RunDetailView,
  swapping `Run` for a new session model.
- Right-hand action panel: "Resume" (writes a `.claude/cwd` shim and
  spawns `claude --resume <sessionId>` either externally or in F-1).

**Open questions:**
- Are old sessions (>30 d) worth indexing or should we cap?
- Cross-project: only show Scout-related projects, or every Claude Code
  project the user has ever opened?
- Does this duplicate the per-run Tools tab inside Control Center
  RunDetailView? (Probably not — that's "session linked to a Scout
  scheduled run"; this is "every session, scheduled or not".)

---

## F-3. Agents page

**Ask:** "Then, I want a […] 'agents' page with a list of all of my
recent claude code sessions so I can revisit them to see what's going
on."

**Interpretation (needs confirmation):** F-2 already covers raw
sessions. "Agents" might mean either:
  - **(a) Subagents/skills**: which Claude Code subagents have been
    used, with their last-run summary (works the same way ToolsTab
    surfaces tool counts but at the subagent layer).
  - **(b) Scout's own scheduled agents** (briefing, consolidation,
    dreaming, research) consolidated with a "what each one did
    recently" view. Largely overlaps with the existing Schedules page,
    but reframed: instead of "when does it run", "what has it done".
  - **(c) Custom agent definitions (`.claude/agents/*.md`)** managed
    in-app: create / edit / clone / sync to disk.

**Most likely:** (b) — the user has been thinking of Scout's
briefing/dreaming/etc. as "my agents" throughout the conversation, and
a "what has each agent been doing for me" page is more interesting than
a chat-session log.

**Approach (if (b)):** New sidebar item "Agents" with one card per slot
type. Each card shows: most-recent run + status, last 7 d activity
sparkline, links to "browse this agent's sessions" (F-2 with prefilter)
and "see schedule" (existing Schedules page).

**Action:** Ask the user which interpretation they meant before
building.

---

## F-4. Session ↔ Action-item task linkage

**Ask:** "We should have the app tie the sessions to the tasks so I can
quickly and easily go from scout app → to resuming a session for a
particular task."

**Why it matters:** The "Launch Claude" button on a task already feeds
the task prompt into Claude. But there's no return link: open the same
task tomorrow, click Launch again, and you start a fresh session that
has no memory of yesterday's. Linking the session id to the task makes
"Resume" the natural next step.

**Approach:**
- New small store: `~/Scout/.scout-state/task-sessions.json`. Schema:
  `{ "<task-subject-hash>": [ {"session_id":"…","launched_at":"…","cwd":"…"} ] }`
- `ClaudeLauncher` updated to record the session id on launch. Reading
  the session id requires either (a) parsing the `claude` stdout for
  the announcement line or (b) tailing the project dir for a new
  `.jsonl` after we spawn it, then matching on cwd + start time.
- `TaskCardView` (action-items) gains a "Resume" button when the task
  has linked sessions. Click → "X sessions linked — most recent at
  HH:MM" menu, pick one, resume.
- The Sessions page (F-2) row gets a "↔ task name" badge when a
  pointer exists, hot-linking back to the task.

**Open questions:**
- Hash key: subject text is fragile (renames break the link). Need a
  stable id — Scout's plugin already assigns each task an md5
  fingerprint when it writes the markdown; reuse that.
- Does the link survive across days (carryforward tasks)? Probably yes
  — the fingerprint is content-derived.
- UI surface for "browse all of this task's sessions"?

---

## Suggested execution order

1. **Bugs first** — finish clearing the control-center / action-items
   audit. The remaining items in `control-center-bugs.md` are the
   priority before any new sidebar items appear.
2. **F-4 (session↔task linkage)** — small, doesn't add a new sidebar
   item, makes F-1 and F-2 dramatically more useful when they land.
3. **F-2 (Sessions page)** — once F-4 is in, the Sessions page has
   meaningful task-linkage data to render.
4. **F-3 (Agents page)** — confirm the interpretation with the user
   first; might be a small variant of the Schedules page rather than a
   new build.
5. **F-1 (Embedded terminal)** — biggest lift; saves until the
   sessions/tasks plumbing is in place so "Resume" inside the terminal
   actually means something.
