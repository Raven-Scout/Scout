# Clickable summary chips (Action Items List view)

**Date:** 2026-06-15
**Status:** Approved design

## Problem

In the Action Items **List** view, each collapsed task card shows a grey summary
chip row beneath its title — e.g. `1 PR`, `keboola/crm`, `Linear`, `Slack`,
`carried Jun 2`. These chips are derived from the task's deep links but are
purely static text. The user expects them to be clickable and open the
corresponding PR / Linear issue / Slack thread, the same way the blue inline
`#925` references and the expanded `TaskLinksView` chips already do.

Today only *some* affordances on a card open something:
- Inline GitHub refs (`#925`) — clickable via `GitHubRefLinkifier` / `InlineMarkdownText`.
- "Context" people links — clickable.
- Expanded-detail `TaskLinksView` chips — clickable, but only visible when expanded.
- **Summary chip row (`TaskChip`) — NOT clickable.** ← this spec.

## Scope

In scope:
- The summary chip row rendered by `chipRow` in
  `Scout/ActionItems/Views/TaskCardView.swift`.
- The chip-derivation logic and model in
  `Scout/ActionItems/Views/TaskChip.swift`.
- Unit tests in `ScoutTests/ActionItems/TaskChipTests.swift`.

Out of scope (unchanged):
- `ActionItemsParser`, `TaskDeepLink`, `TaskLinksView`.
- The **Board** view (`BoardCardView`) footer — a separate surface. Noted as a
  possible follow-up, not part of this change.

## Decisions (confirmed with user)

- **Multi-item chips** (a chip summarising several items, e.g. `2 PRs`,
  `3 Linear`): clicking shows a **small dropdown** listing each item; selecting
  one opens it.
- **Repo chip** (`keboola/crm`): opens the **GitHub repo homepage**
  (`https://github.com/<repo>`), distinct from the PR chip.
- **Carry chip** (`carried Jun 2`): has no target — stays static.

## Design

### 1. `TaskChip` carries its click targets

`TaskChip` currently holds only `glyph` + `label`. Add a `links` array so the
chip knows where it points. A nested `Link` pairs a menu label with a URL.

```swift
struct TaskChip: Identifiable, Equatable {
    enum Glyph: Equatable { case github, linear, slack, carry }

    struct Link: Identifiable, Equatable {
        let label: String          // shown as a dropdown item, e.g. "PR keboola/crm#925"
        let url: URL
        var id: String { url.absoluteString }
    }

    let glyph: Glyph
    let label: String
    let links: [Link]              // 0 = static · 1 = open directly · >1 = dropdown

    // Defaulted `links` keeps existing call sites and tests
    // (e.g. TaskChip(glyph: .carry, label: "carried Jun 2")) compiling and Equatable-equal.
    init(glyph: Glyph, label: String, links: [Link] = []) { ... }

    var id: String { label }
}
```

### 2. `TaskChip.chips(for:)` populates `links`

The derivation stays the single source of truth (already unit-tested). Order is
unchanged: GitHub → Linear → Slack → carry.

- **PR count chip** (`1 PR` / `N PRs`): one `Link` per GitHub PR deep link,
  `label = deepLink.displayLabel` ("PR repo#n"), `url = rawURL`.
- **Repo chip** (only when all PRs share one repo): one synthetic `Link`,
  `label = repo`, `url = URL(string: "https://github.com/\(repo)")`. Attached
  only if the URL builds (it always should for a `owner/name` slug).
- **Linear chip** (`Linear` / `N Linear`): one `Link` per Linear deep link,
  `label = displayLabel` ("Linear AI-1"), `url = openURL`.
- **Slack chip** (`Slack` / `N Slack`): one `Link` per Slack thread,
  `label = displayLabel` ("Slack thread"), `url = thread URL`.
- **Carry chip**: `links = []`.

### 3. Rendering — `chipRow` branches on `links.count`

`chipRow` maps each chip through a `chipView(for:)` builder:

- **`links.count == 0`** → the current static chip (glyph + mono label +
  `EditorialChipBackground`). Unchanged appearance.
- **`links.count == 1`** → a `Button` that calls
  `NSWorkspace.shared.open(chip.links[0].url)`, styled `.buttonStyle(.plainHit)`,
  with `NSCursor.pointingHand` on hover (the existing `TaskActionsView` pattern)
  and `.help(url.absoluteString)`.
- **`links.count > 1`** → a `Menu` whose label is the chip body and whose content
  is one `Button` per `Link` (`Button(link.label) { NSWorkspace.shared.open(link.url) }`).
  The menu indicator is hidden (`.menuIndicator(.hidden)`) to keep the chip
  compact; pointing-hand on hover.

The chip body (glyph + label + chip background) is extracted to a shared
sub-view so all three branches render identically.

### 4. Tests (`TaskChipTests`)

Add assertions alongside the existing ones:
- Single PR → chip's `links` has one entry whose `url` is the PR `rawURL`.
- Repo chip → its `Link.url == https://github.com/<repo>`.
- Two PRs (same repo) → PR count chip `links.count == 2`; repo chip present with
  one link.
- Carry chip → `links.isEmpty`.

Existing tests (`carryChipAppended`, ordering, counts) keep passing because
`links` is defaulted and the chip's `id`/`Equatable` semantics for the carry
case are unchanged.

## Error handling

- Building the repo-homepage URL uses `URL(string:)`; if it returns `nil` the
  repo chip simply gets no link (renders static). Not expected for valid slugs.
- Opening always goes through `NSWorkspace.shared.open`, matching existing link
  behaviour; failures are handled by macOS as today (no in-app error surface,
  consistent with `TaskLinksView`).

## Testing strategy

Pure derivation logic is covered by `TaskChipTests` (model). The view wiring
(`chipRow` branching) is thin glue over the model and the established
`.plainHit` / `Menu` patterns; verified by building and running the app.
