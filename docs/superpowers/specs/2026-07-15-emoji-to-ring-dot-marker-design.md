# Replace emoji color-dots with a ring+dot marker (+ decorative-emoji sweep)

**Date:** 2026-07-15
**Status:** Approved (design)

## Problem

The app leans on emoji as UI chrome. The most prominent case is the section-kind
"color dots" (🔴🟡🟢) and category pictographs (🏡💡📅📋) rendered in Action Items
section headers, plus flat drawn color dots in board column headers and filter
chips. Emoji render inconsistently across machines, don't respect the editorial
palette, and read as unpolished against the warm-paper / ink design system.

## Goal

Replace decorative emoji with a single, palette-native **ring+dot marker** (a soft
tinted ring wrapping either a solid colored dot or a small SF Symbol), and sweep
the remaining decorative emoji / glyph-as-icon usages to SF Symbols — without
touching the emoji that the parser and data layer depend on.

## Non-goals

- **Do not touch parser/data emoji.** These emoji are matched against the markdown
  `scoutctl` writes into the vault; changing them breaks parsing and violates the
  parser-contract / public-fixture rules in `CLAUDE.md`. Sites:
  - `Scout/ActionItems/ActionItemsParser.swift:252` — snooze regex (`🛌 Snoozed until …`)
  - `Scout/ActionItems/ActionItemsParser.swift:525` — `recognizedEmojiPrefixes`
  - `Scout/ActionItems/ActionItemsParser.swift:554–560` — emoji → `Kind` map
  - `Scout/ActionItems/Models/ActionTask.swift:128` — priority-emoji strip
  - `Scout/ActionItems/Models/ActionTask.swift:133` — status-emoji strip
- **Keep legitimate typography.** `·` separators, `…` ellipses, `⌘⇧↵` keyboard
  hints, `→` in prose, and list bullets `•` are correct typographic marks, not
  emoji. Converting them would degrade the type system.
- No change to `parser-corpus.json` or any fixture (this work does not alter
  parsing), so the three-repo corpus sync / checksum dance does not apply.

## Design

### 1. `KindMarker` view (new)

A small reusable SwiftUI view. Two forms, split on whether the kind carries a
symbol:

- **Priority / neutral kinds** (dot form) → concentric marker matching the
  reference screenshot: an outer ring `Circle().strokeBorder(hue.opacity(0.4),
  lineWidth: 1.5)` around a solid `Circle().fill(hue)` at ~0.42·`size`.
- **Category kinds** (icon form) → a bare, full-size `Image(systemName: symbol)`
  tinted `hue` at `size` pt, `.medium` weight, no ring. (Refined from an
  earlier "symbol inside the ring" idea, which rendered the glyphs too small to
  read; a bare full-size icon is legible, and the ring then reads specifically
  as a priority dot.)
- **Parameters:** `kind: ActionSection.Kind` and `size: CGFloat` (default 14).
- **Color:** `hue = DS.priorityColor(kind)` — no new colors introduced; light/dark
  handled by the existing palette.
- **Location:** `Scout/Utilities/DesignSystem.swift` (or a sibling
  `Scout/Utilities/KindMarker.swift`), next to the palette it depends on.

Sizes in use: section header & board header = 14px, filter chip = 12px.

### 2. `DS.kindSymbol(_:)` mapper (new)

Mirrors the existing enum→SF-Symbol pattern (`chipGlyph`, `linkGlyph`, `iconName(for:)`).
Returns `nil` for kinds that render as a plain colored dot.

| kind      | symbol            |
|-----------|-------------------|
| urgent    | nil (dot)         |
| todo      | nil (dot)         |
| watching  | nil (dot)         |
| neutral   | nil (dot)         |
| done      | `checkmark`       |
| personal  | `house`           |
| focus     | `lightbulb`       |
| meetings  | `calendar`        |
| digest    | `list.clipboard`  |

### 3. Surface integrations

1. **Section headers** — `Scout/ActionItems/Views/SectionView.swift:48`.
   Replace `Text(glyph)` with `KindMarker(kind: section.kind, size: 14)` inside the
   existing 18px leading frame. **Remove the `glyph` computed property
   (lines 74–76)**, which means the header no longer renders the free-form
   `section.emoji` string — the marker is always derived from `kind`, so no emoji
   can leak from vault content into a header.
2. **Board column headers** — `Scout/ActionItems/Views/BoardView.swift:56`.
   Replace the `Circle().fill(priorityColor).frame(8×8)` with
   `KindMarker(kind: column.kind, size: 14)`.
3. **Filter chips** — `Scout/ActionItems/Views/FilterChipsView.swift:97` and the
   `chipButton(dot:)` render block. Change the `dot` parameter from `Color?` to
   carry the kind (e.g. `kind: ActionSection.Kind?`) and render `KindMarker` in
   place of the current flat circle+overlay. The "all" chip stays dot-less.

Once all three are migrated, **delete `DS.kindGlyph`** (`DesignSystem.swift:103–115`).

### 4. Decorative-emoji / glyph-as-icon sweep

Replace with `Image(systemName:)`, matched to nearby styling (font size / color):

- `Scout/ActionItems/Views/SectionView.swift:148` — completed marker `✓` → `checkmark`.
- `Scout/ControlCenter/ConnectorHealthRailCard.swift:52` — `⚠` → `exclamationmark.triangle`.
- `Scout/ControlCenter/ConnectorHealthRailCard.swift:90` — uptime `✓` label → `checkmark`.
- `Scout/ControlCenter/ConnectorHealthRailCard.swift:129/130/132` — `✓`/`✗`/`·`
  → `checkmark` / `xmark` / `circle` (keep the existing `DS.Status.*` / `DS.Ink.p4` tints).
- `Scout/ControlCenter/ConnectorAlertBanner.swift:28` — `⚠` → `exclamationmark.triangle`.
- `Scout/ControlCenter/NowStripView.swift:126` — `✓`/`●`/`✗` → `checkmark` / `circle.fill` / `xmark`.
- `Scout/PerFileItems/Views/PerFileListView.swift:93/113` — fullwidth `＋` in
  empty-state copy → plain `+` (or reference the toolbar symbol name).

**Left as text (accessibility/tooltip strings, not icons):**
`ActivityHeatmapView.swift:248/249` (`✓`/`✗` inside VoiceOver/tooltip strings) and
the `•` bullets in `SummaryTab.swift:126` / `KBEditableView.swift:201/203` stay —
they are content, not chrome.

## Testing / verification

- Build for macOS via xcodebuild (see memory: `DEVELOPER_DIR=…Xcode-26.5.0…`).
- Run `ParserContractTests` (platform=macOS) to prove the parser side is untouched.
- Launch the app and eyeball, in **both light and dark**: Action Items section
  headers (every kind), the board view column headers, the filter chips, and the
  Control Center connector-health / now-strip status glyphs.

## Risks

- **`section.emoji` behavior change.** Dropping the emoji-render path means a vault
  section carrying a custom, unmapped emoji falls back to its kind marker (neutral
  if unknown). This is intended — it guarantees no emoji in headers — but is a
  visible behavior change worth calling out.
- **Filter-chip signature change.** `chipButton` currently takes `dot: Color?`;
  moving it to a kind changes the "all" / non-kind chips. Verify none rely on an
  arbitrary color that isn't kind-derived.
- **SF Symbol availability.** `list.clipboard` requires a recent SF Symbols set;
  confirm it renders on the deployment target, else fall back to `doc.plaintext`.
