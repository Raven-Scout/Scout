# Scout.app Wishlist & Research tabs — design

Two new Scout.app sidebar tabs — **Wishlist** and **Research** — that surface
the per-file items the plugin now writes (`docs/wishlist/` and
`knowledge-base/research-queue/`), let the user **add** new items, and **resolve**
(mark done/dropped) existing ones. Built on a shared generic per-file core, a
near-copy of the just-shipped Proposals feature.

Status: design approved 2026-06-19, pending spec review.

This is **sub-project 3** of the per-file Wishlist & Research Queue restructure
(spec: `2026-06-16-wishlist-research-queue-per-file-design.md`). Sub-projects 1
(schema) and 2 (plugin distribution, scout-plugin PR #144) are merged; the
per-file format now reaches all users on `/scout-update`.

---

## Motivation

The user wants to add wishlist items and research-queue topics from Scout.app,
and to see the queues at a glance. The plugin now stores each item as a per-file
markdown document with YAML frontmatter (mirroring `dreaming-proposals/`), so the
app can parse and write them robustly — exactly the shape the Proposals feature
already handles. This sub-project ports that feature, generalized, to the two new
directories, and adds an "add item" writer.

**Critical constraint (no drift):** these tabs MUST read exactly the frontmatter
the plugin migration/sessions write (the same lesson as the empty-Proposals-tab
bug, #38). The shared per-file schema below is the contract.

---

## Shared per-file schema (the contract with the plugin)

Each item is `YYYY-MM-DD-slug.md` with YAML frontmatter + body:

```markdown
---
title: <one-line title>
status: open | in-progress | done | dropped
priority: urgent | high | medium | low
date: YYYY-MM-DD
source: <provenance, optional>     # wishlist only
area: <grouping, optional>         # research only
---

# <title>

<body: description / rationale / notes / findings>
```

- `status`: `open`/`in-progress` are **active** (Awaiting); `done`/`dropped` are
  **resolved**.
- `priority`: Wishlist uses `high`/`medium`/`low`; Research adds `urgent` (the
  🔴 START-IMMEDIATELY level).
- Identity is the file path; the leading `# <title>` H1 is stripped on render
  (the card header shows the title), matching the Proposals parser.
- The thin `knowledge-base/research-queue.md` run-log lives in the **parent** of
  `research-queue/`, so the directory scan never sees it; defensively, files
  without frontmatter parse to `nil` and are skipped.

---

## Architecture — shared generic core + two thin tab configs

A new `Scout/PerFileItems/` module holds the generic layer. **Wishlist and
Research are configuration values, not new Swift types.** The existing Proposals
feature is left untouched (it has a more divergent vocabulary and was recently
fixed in #38; retrofitting it onto this core is an optional future cleanup).

```
Scout/PerFileItems/
  Models/
    PerFileItem.swift          # the item struct
    ItemStatus.swift           # open|in-progress|done|dropped
    ItemPriority.swift         # urgent|high|medium|low
    MarkdownBodyBlock.swift    # prose/code body blocks (self-contained copy of Proposals')
  PerFileItemParser.swift      # pure: contents -> PerFileItem?
  PerFileDocumentService.swift # @MainActor ObservableObject, FSEvents
  PerFileItemWriter.swift      # actor: addItem + setStatus
  PerFileTabConfig.swift       # the per-tab knobs (+ .wishlist / .research values)
  Views/
    PerFileListView.swift      # list + awaiting/resolved split + toolbar
    PerFileItemCardView.swift  # one card + Done/Drop actions
    AddItemSheet.swift         # add-item form
    ItemStatusPill.swift
    ItemPriorityPill.swift
    MarkdownBodyView.swift     # renders [MarkdownBodyBlock]

ScoutTests/PerFileItems/
    PerFileItemParserTests.swift
    ItemStatusPriorityTests.swift
    PerFileItemWriterTests.swift
```

### `PerFileTabConfig`
Carries everything that differs between the two tabs:
- `directoryDefault` (e.g. `~/Scout/docs/wishlist`) + the `UserDefaults` key for
  the override (`wishlistPath` / `researchQueuePath`).
- `sidebarItem` case, sidebar label, SF Symbol icon.
- `priorities: [ItemPriority]` — `[.high, .medium, .low]` (Wishlist) /
  `[.urgent, .high, .medium, .low]` (Research). Constrains the Add-form picker.
- `defaultPriority` (`.medium`).
- `optionalField` — `.none` / `.source(label:)` / `.area(label:)`: which extra
  frontmatter field the Add form collects and the card shows.
- `addCommitNoun` — "wishlist item" / "research topic" (for commit messages).

`PerFileTabConfig.wishlist` and `.research` are the two instances.

---

## Components

### Models
- **`PerFileItem`** (`Identifiable, Equatable, Sendable`): `fileURL` (identity =
  `fileURL.path`), `date`, `title`, `status: ItemStatus`, `priority:
  ItemPriority`, `source: String?`, `area: String?`, `bodyMarkdown`. Computed:
  `isActive` (= `status.isActive`), `bodyBlocks: [MarkdownBodyBlock]`.
- **`ItemStatus`**: `open | inProgress | done | dropped`; `isActive` true for
  `open`/`inProgress`; tolerant `parse(_ raw:) -> ItemStatus` (defaults to `open`
  if unrecognized/missing); `displayName`.
- **`ItemPriority`**: `urgent | high | medium | low`, `Comparable` (urgent
  highest) for sorting within a section; `parse(_:)` defaults to `medium`;
  `displayName`.
- **`MarkdownBodyBlock`**: `.prose(String)` / `.code(language:String?, code:String)`
  — a self-contained copy of `ProposalBodyBlock`'s pure parsing (~90 lines), so
  the core doesn't depend on the Proposals module.

### `PerFileItemParser` (pure, `nonisolated enum`)
Generalizes Proposals' already item-agnostic primitives:
- `splitFrontmatter(_:) -> (frontmatter, body)?`
- `parseFrontmatterFields(_:) -> [String:String]` (keys lowercased, quotes stripped)
- `stripLeadingHeading(_:)` (drops one leading `# …`, not `##`)
- `datePrefix(of stem:) -> String?`
- `parseFile(contents:fileURL:) -> PerFileItem?` — `nil` when no frontmatter.
  Extracts title (frontmatter, else filename stem), date (frontmatter, else
  filename prefix), status/priority via the tolerant enum parses, and
  `source`/`area`. Pure → unit-tested.

### `PerFileDocumentService` (`@MainActor final class … ObservableObject`)
Near-copy of `ProposalsDocumentService`, parameterized by `directoryURL`:
- `@Published items: [PerFileItem]`, `@Published state` (idle/loading/loaded/
  missing(URL)/failed).
- `load()` / `reload()`: list `*.md` (non-recursive), sort **reverse
  lexicographically** (date-prefixed filenames → reverse-chronological), parse
  each (skip `nil`), publish.
- Watches `directoryURL` via the existing `FileSystemEventSource` protocol
  (250 ms debounce); filters `.md`. So session/Obsidian writes appear live.
- `activeCount: Int` (= `items.filter(\.isActive).count`) for the sidebar badge.

### `PerFileItemWriter` (`actor`)
- `addItem(title:priority:body:source:area:in directoryURL:noun:) async throws -> URL`:
  - filename = `<today>-<slugify(title)>.md`, with `-2`/`-3` suffix on collision
    (same `_unique_path` logic as the engine migration).
  - body = pure `renderItemFile(...)` → frontmatter (`title` double-quoted,
    `status: open`, `priority`, `date`, optional `source`/`area`) + `# <title>` +
    body. (Mirrors the engine migration's `render_item` + Proposals' write path.)
  - atomic write, then git-commit the single path: `app: add <noun> <title>`.
- `setStatus(fileURL:newStatus:) async throws`: pure `rewriteFrontmatterStatus`
  (find the `status:` line in frontmatter, replace value, preserve everything
  else — same pattern + error types as `ProposalsWriter`), atomic write,
  git-commit `app: mark <title> <done|dropped>`.
- Serialized via the actor; pure helpers (`renderItemFile`, `slugify`,
  `rewriteFrontmatterStatus`) are unit-tested.

### Views
- **`PerFileListView(config:)`** — header (config label + "N active" subtitle);
  toolbar **＋ Add** (opens `AddItemSheet`) and **Reveal in Finder**; content
  switches on service `state`; loaded content splits `items` into Awaiting
  (`isActive`) and a collapsible **Resolved (N)** section; empty/missing states.
  Near-copy of `ProposalsView`.
- **`PerFileItemCardView`** — header: date chip + title + `ItemPriorityPill` +
  `ItemStatusPill`; `MarkdownBodyView(blocks:)`; for **active** items, two small
  buttons **Done** and **Drop** (mirroring Proposals' Approve/Decline), with
  local in-flight + error state, feeding `writer.setStatus`.
- **`AddItemSheet(config:)`** — form: **Title** (required, Add disabled when
  empty), **Priority** picker (from `config.priorities`, default
  `config.defaultPriority`), **Body** (multiline `TextEditor`), and the optional
  field (Source / Area) when `config.optionalField != .none`. Submit →
  `writer.addItem` → dismiss → service reload.
- **`ItemStatusPill`** / **`ItemPriorityPill`** — color-coded capsules (priority:
  urgent=red, high=orange, medium=neutral, low=gray; status reuses the Proposals
  pill palette).
- **`MarkdownBodyView`** — copy of `ProposalBodyView` over `[MarkdownBodyBlock]`
  (prose via `InlineMarkdownText`, code as monospace panels).

---

## Data flow

1. **Load:** `PerFileListView` appears → `service.load()` scans + parses the dir →
   `@Published items` render (Awaiting / Resolved).
2. **Watch:** FSEvents on the dir → debounced `reload()` → external writes
   (dreaming/research sessions, Obsidian) appear live.
3. **Add:** toolbar ＋ → `AddItemSheet` → `writer.addItem` writes `YYYY-MM-DD-slug.md`
   + git-commit → `service.reload()` → new item under Awaiting.
4. **Resolve:** card **Done**/**Drop** → `writer.setStatus` flips frontmatter +
   commit → reload → item moves to Resolved.

Writes are git-committed with scoped paths (single file), matching
`ProposalsWriter`'s convention.

---

## Sidebar & Settings integration

- Add `.wishlist` and `.research` cases to `SidebarItem` (MainWindowView);
  add two rows to `SidebarView` with `activeCount` badges; add detail branches
  → `PerFileListView(config: .wishlist)` / `.research`.
- Two new path settings following the exact `dreamingProposalsPath` pattern in
  `AppState` + a Settings field each: `wishlistPath` (default
  `~/Scout/docs/wishlist`), `researchQueuePath` (default
  `~/Scout/knowledge-base/research-queue`). Tilde-expanded; applied at launch.

---

## Testing (TDD, Swift Testing — pure-first, mirroring Proposals)

- **`PerFileItemParserTests`**: frontmatter split, field parse, status/priority/
  source/area extraction, H1 strip, date fallback (filename prefix), `nil` on no
  frontmatter.
- **`ItemStatusPriorityTests`**: `ItemStatus.parse` + `isActive`; `ItemPriority.parse`
  + `Comparable` ordering.
- **`PerFileItemWriterTests`**: pure `renderItemFile` (valid + quoted frontmatter,
  optional fields present/absent), `slugify`, collision `-2` suffix, pure
  `rewriteFrontmatterStatus` round-tripping through `PerFileItemParser`; e2e
  `addItem` + `setStatus` against a temp dir with the `ScriptedRunner` git mock.
- Services/views verified by build + the `FileSystemEventSource` test double (as
  with Proposals; `PerFileDocumentService` isn't directly unit-tested, the watcher
  protocol is the seam).

---

## Scope / non-goals (YAGNI)

- App actions are exactly **View + Add + Resolve (Done/Drop)**. No in-progress
  toggle, no editing existing item bodies/priority, no `area`/`source` editing
  after creation — the dreaming/research sessions own ongoing state and findings.
- **Proposals is not refactored.** The new core duplicates ~90 lines of body-block
  parsing rather than depend on the Proposals module; unifying the two is an
  optional later cleanup.
- The app does not run dreaming/research; it views, adds, and resolves — the
  sessions still do the work.
- No new status/priority semantics beyond the shared schema's enums.

---

## Decided defaults (flagged during brainstorming)

- **Body-block parser duplicated** into the core (vs. depending on Proposals'
  type) — keeps the core self-contained and Proposals untouched.
- **Resolve = two buttons** (Done / Drop), mirroring Proposals' Approve/Decline
  (vs. a single menu).
