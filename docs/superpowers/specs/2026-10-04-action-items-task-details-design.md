# Action Items: attach sub-bullet context to its task

**Date:** 2026-10-04
**Status:** Proposed. Review before implementation.

## Problem

Most action items written since about Aug 19 show in the app as a bare title.
The expanded card has nothing in it, and **Launch Claude** sends only the title
and links. The context exists. It sits in the daily file as indented
sub-bullets under the task line, and the app throws those lines away.

The parser builds `ActionTask.body` from one place only: the text after the
first ` — ` / ` – ` / ` - ` (or `: `) on the checkbox line, outside bold, code,
wikilink and link tokens. An indented sub-bullet under a task is attached to
that task only when it is one of three machine shapes:

| Sub-bullet | Becomes |
|---|---|
| `  - Refs: a · b` | `deepLinks` |
| `  - snoozed-until: YYYY-MM-DD` | `snoozedUntil` / `snoozedFromKind` |
| `  - <word>: <text>` and `  > <author>: <text>` | `comments` |

Every other indented line falls through to `bulletRe` or the paragraph branch
and is appended to `ActionSection.bullets`. No task card renders a section's
bullets (only the Focus list and the Digest do), so the content vanishes.

The engine changed how it writes items. A new item now carries its separator
inside the bold title and puts all of its context in emoji-led sub-bullets:

```markdown
- [ ] [#DETX] **Order the roadmap items before Tuesday — you are the decider**
  - 🗓️ The ticket is due Tuesday and has never left Todo.
  - 🔑 The other three items wait on the customer; this one does not.
  - 📦 The nine candidate items are already listed in the ticket.
```

`splitSubjectBody` skips the ` — ` because it is inside `**…**`, so `body` is
`""`. All three sub-bullets go to the section and are never shown.

Measured with the parser's split rules over the real vault's daily files
(counts only):

| Daily file | Open items | Empty body | Empty body, context in sub-bullets |
|---|---|---|---|
| 2026-07-16 | 173 | 0 (0%) | 0 |
| 2026-08-15 | 279 | 26 (9%) | 9 |
| 2026-08-20 | 359 | 81 (22%) | 64 |
| 2026-09-14 | 762 | 203 (26%) | 185 |
| 2026-10-04 | 944 | 332 (35%) | 314 |

On 2026-10-04, 67 of 102 items in 🔴 Urgent and 11 of the first 12 items in the
file have an empty body. The items that still have a body are mostly old ones
carried forward from earlier days. The file holds 3,167 indented non-task
sub-bullets, 161 of them nested two or more levels deep, and 132 indented
continuation lines (fenced code inside a sub-bullet, wrapped text).

### This is the app falling behind the engine

- scout-plugin's own parser (`engine/scout/action_items/parser.py`) already
  collects every indented sub-bullet under an item into `ActionItem.details`.
- The phase text (`phases/core/action-items.md`, *Clean Title, Prose Body, Refs
  Block*) defines the body as the text after the ` — ` **plus `- Source:` /
  `- Context:` sub-bullets**.

The app is the only reader that drops them.

## Goal

Every indented line under a task belongs to that task. Show it on the card and
put it in every prompt the app builds, so an item written in either shape
(one-line body or sub-bullets) gives the same context.

## Non-goals

- **No change to `body` or the parser contract.** `parser-corpus.json` pins one
  task line per entry and compares `short_prefix`, `subject`, `plain_subject`
  and `body` only. Details are a new field next to `body`, so the three-repo
  corpus and both checksums stay as they are.
- **No change to comment classification.** Today `  - Links: …`,
  `  - Source: …` and `  - Context: …` are read as comments from "Links",
  "Source" and "Context". On 2026-10-04 that is 117 `Links:`, 5 `Source:` and
  3 `Context:` lines. That is wrong, but comment edit and delete go to scoutctl
  **by index** (`CommentSelector.index`), and scoutctl's
  `_common.list_comment_lines` counts those lines as comments too. Reclassifying
  them only in the app would make edit and delete hit the wrong comment, so the
  fix needs scoutctl and the app to change together. Out of scope; see
  *Follow-ups*.
- **No engine-side authoring rule.** It is worth having, but it cannot recover
  the 300+ items already written. See *Follow-ups*.
- No markdown block rendering (headings, tables, real code blocks) inside
  details. Details render inline-only, like `body` does today.

## Design

### Model

```swift
/// One indented line of context under a task: a sub-bullet, or a run of
/// continuation lines that belong to the sub-bullet above it.
nonisolated struct TaskDetail: Equatable, Hashable, Sendable {
    /// Nesting below the task: 0 = direct sub-bullet, 1 = sub-sub-bullet …
    let depth: Int
    /// Raw markdown, without the bullet marker. Continuation lines are joined
    /// with "\n", with the bullet's own indent removed so code inside a fence
    /// keeps its relative indentation.
    let text: String
}
```

`ActionTask` gains `let details: [TaskDetail]`. It is a **required** init
parameter with no default. The parser rebuilds the task in five places (one
for each comment/refs/snooze/inline-comment branch), and a defaulted parameter
would let any of those rebuilds silently reset `details` to `[]`. That is the
exact failure the "parser rebuild paths swallow new fields" lesson describes.
With no default, the compiler lists every construction site.

`ActionTask` also gains one derived property:

```swift
/// What a one-line surface shows under the title: the body when the task line
/// has one, otherwise the first line of the first detail.
var summary: String
```

### Parser: the detail window

There is one new piece of state, `detailWindowOpen: Bool`.

- **Opens** on a task line, top-level or nested. `currentTasks.last` is the
  owner.
- **Stays open** across blank lines (markdown allows loose lists), and across
  every sub-line already attached to the task (Refs, snooze, comments, inline
  comments). Those branches still run first and still `continue`, so their
  behaviour does not change.
- **Closes** on any line that has no leading whitespace and is not blank: a
  top-level bullet, a paragraph, an HTML comment, `---`, a table row, a
  `<details>` tag line, a `###` subhead or a `##` header. `flushSection()` and
  `closeCollapsedGroup()` also close it. A new task line re-opens it for the
  new owner.

While it is open, an **indented** line that no earlier branch took is handled
like this:

1. **Indented bullet** (`-`, `*` or `+`, then a space, and not a checkbox):
   append `TaskDetail(depth:text:)`. `depth` is
   `max(0, indentLevelFor(indent) - owner.indentLevel - 1)`, so a nested
   child task's own sub-bullets are depth 0 under that child.
2. **Any other indented, non-blank line** (wrapped text, a fence marker, code
   inside a fence, a `>` line that is not a well-formed comment): append it to
   the last detail's `text` after `"\n"`. Strip up to `bulletIndent + 2` leading
   spaces, so code inside a fence keeps its indentation relative to the fence.
   If the task has no detail yet, start a depth-0 detail with the trimmed line.

The new branch goes immediately **before** `// Bullet (section-level)`, after
every existing sub-line branch. The precedence for an indented line is
therefore: task, quote comment, snooze, Refs, dash comment, inline comment,
**detail**, then section bullet or paragraph.

The `<details>` archive regions need no special handling. Tasks parked inside
a region are ordinary `ActionTask`s and get details the same way. The tag line
itself closes the window, so a region's tag never attaches to the task above
it.

Fences: an indented line that **is** a fence marker counts as continuation
(rule 2). While a fence is open, lines inside it count as continuation too,
even an indented `- foo` inside a code block. Track this with an
`inDetailFence` flag, toggled by an indented line whose trimmed text starts
with ```` ``` ```` or `~~~` and reset whenever the window closes. (`main` has
no fence handling in the parser yet, so this adds a small private helper
rather than reusing one.)

### Card (`TaskCardView`)

- **Collapsed teaser:** show `task.summary` instead of `task.body` (2 lines,
  same style). Items written with sub-bullets get their description line back.
- **Expanded detail:** after `TaskBodyView`, a new `TaskDetailsView(details:)`
  draws each detail as a muted bullet row, indented `CGFloat(depth) * 14`,
  text via `InlineMarkdownText` (inline-only, whitespace-preserving, so
  continuation newlines survive). It is shown only when `details` is non-empty.
  Comments, links, actions and the composer stay in their current order below
  it.
- **Nested sub-task row:** show `task.summary` instead of `task.body`.

`BoardCardView` shows no body today, and that stays as it is.

### Search (`ActionItemsView.filtered`)

`matches` also checks `details.contains { $0.text.lowercased().contains(needle) }`.
Otherwise a search for a word that only appears in the sub-bullets would hide
the item.

### Prompts (`ClaudeLauncher`)

| Format | Today | After |
|---|---|---|
| `.fullContext` (Launch Claude, Copy) | subject, body, comments, links | subject, body, **`Context:` list of details**, comments, links |
| `.concise` | subject + body | subject + `summary` |
| `.markdownChecklist` | checkbox + body lines + links | checkbox + body lines + **details as nested bullets** + links |

The context list renders each detail as `"  " * depth + "- " + firstLine`.
Continuation lines are indented to sit under their bullet. Multi-task prompts
reuse `fullContextBody`, so they get the change automatically.

## Risks

- **Prompt length on the Claude Desktop path.** `makeDesktopURL` puts the
  prompt in a `claude://…?q=` query. Items written with sub-bullets can run
  several KB, and emoji triple in size when percent-encoded. A single
  9,700-character task line already flowed through this path, so long prompts
  are not new, but nobody has measured the limit. Plan: assert in a unit test
  that `makeDesktopURL` builds a URL for a 40,000-character prompt, and launch
  the largest real item by hand in Desktop as part of verification. If Desktop
  truncates, record it and open a follow-up (a clipboard fallback, like the
  CLI path). This PR does not change the launch mechanism.
- **Perf.** The parse adds one branch per indented line (~3,300 on a real day,
  a small cost next to the existing per-line regexes). The view does more work
  only for expanded cards. Check `PerfHarnessTests` before and after. Open PR
  #126 (paginating section rows) touches `SectionView` / `ActionItemsView`
  but not the card or the parser, so it should merge cleanly either way.
- **Overlap with open PRs.** #122 edits `TaskCardView` (composer palette, board
  menu). This change touches the teaser line, `detail` and `nestedRow`. A text
  conflict is likely, with no change in meaning. Rebase whichever lands
  second. #118 (display profile) plans which card fields show. `details` is
  one more field for it to consider; nothing here blocks it.
- **Lines that were section bullets before.** An indented bullet under a task
  could, in theory, have been meant as section prose. In the real file every
  indented non-task bullet in a task section follows a task. The window closes
  on the first non-indented line, so a top-level bullet after a task still
  stays section prose.

## Testing

- Parser unit tests (`TaskDetailsParserTests`) cover: plain sub-bullets,
  nested depth, a nested child task owning its own details, details
  interleaved with Refs, snooze and comments with no loss in either direction,
  the window closing on a top-level bullet, a paragraph and an HTML comment,
  blank lines inside the list, continuation lines, a fence with an indented
  `- ` inside it, a parked task inside `<details>`, and a task line with both
  a body and details.
- A rebuild-preservation test: a task with details followed by each of the four
  rebuilding sub-lines keeps its details.
- `ParserContractTests` stays green and the corpus stays byte-identical. This
  is the check that `body` did not move.
- `ClaudeLauncherPromptTests` covers the three formats with details, and the
  ordering relative to comments and links.
- `TaskChipTests` / `ComponentSmokeTests` build cards with details and an
  empty body.
- Search: a filter test where the needle appears only in a detail.
- Manual check against the real vault: launch the Debug build, open the
  2026-10-04 file, expand three sub-bullet-style 🔴 items and one old
  one-line-body item, use **Copy → Full context** on each, and launch the
  longest one in Claude Desktop.

## Follow-ups (not in this PR)

1. **Metadata sub-bullets read as comments.** Move `Links`, `Source`,
   `Context`, `Evidence`, `Completed`, `Originally from` and `Current status`
   from comments to details in **both** readers at once. scout-plugin's
   `render.py` already has a `COMMENT_METADATA_KEYS` list for this. The app's
   `subBulletCommentRe` and scoutctl's `_common.list_comment_lines` must adopt
   it together, or index-based comment edit and delete go out of step.
2. **Engine authoring check.** Add a check that rejects a new item whose bold
   title contains ` — ` with nothing after the bold. A long title belongs
   before ` — `, and the context belongs after it or in sub-bullets.
3. **scout-plugin#269 / Scout#120 (one line per item, context in a linked
   note).** If that ships, the card and prompt should also pull in the linked
   note's content, or this regression comes back by design. That decision
   belongs on #269.
