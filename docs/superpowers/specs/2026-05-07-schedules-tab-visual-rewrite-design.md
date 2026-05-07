# Plan 7 — Schedules Tab Visual Rewrite

**Spec status:** draft 2026-05-07. Brainstormed via `superpowers:brainstorming` against Jordan-supplied mockups (table view + cards view) and the existing scout-app design system at `Scout/Utilities/DesignSystem.swift`.

## 1. Position in the arc

| Plan | Surface | Status |
|---|---|---|
| Plan 5 | Schedule v2 engine + scout-app dispatcher refactor | Shipped |
| Plan 6 | Schedules tab functional rewrite (CRUD via `scoutctl schedule list --json`) | Shipped |
| **Plan 7 (this spec)** | Schedules tab **visual** rewrite — Table + Cards layouts on `DS`, master/detail edit pattern, type-color palette, filter chips, header refresh | — |
| Plan 7-polish (followup) | Settings tab onto DS (only other surface still off DS) | Future |
| Plan 8 (deferred) | Timeline view; remote slot execution + routines management (the `runtime: remote` schema slot reserved in Plan 6 §8) | Future |

This spec is **UI-only**. The Plan 6 service layer (`ScheduleEditService` + `Slot` model + `SlotDraft` + `scoutctl schedule list/save/validate`) is unchanged. Tests for the service stay green; only view-layer test files churn.

## 2. Goals & non-goals

**Goals**

1. Schedules tab matches the editorial DS language already used by Control Center and Action Items: warm paper backgrounds, serif title, mono identifiers, sans body, type-color-coded slots.
2. Two view modes: **Table** (default) and **Cards**. View toggle exposes a third disabled `Timeline` chip with a "Coming in a future plan" tooltip.
3. Master/detail edit pattern via `NavigationSplitView`. Click a row → `SlotEditForm` renders in the detail pane.
4. Filter chips: `All` + per-type chip with live counts + colored type dot.
5. Header: serif title, live subtitle (`N active · M types · now HH:MM`), view toggle, `+ New` button.
6. Existing Plan 6 features preserved end-to-end: stale-edit banner, error banner, type-change confirm, delete confirm, save/revert, draft-row-in-master flow, empty state.

**Non-goals (explicit)**

- Sidebar redesign (`DAYS` / `FILTERS` sections in mockup #1) — out of scope; current sidebar stays.
- Settings-tab DS adoption — separate Plan-7-polish PR.
- Timeline view layout — disabled chip; design + build deferred to a future plan.
- Sortable / filterable columns — table single-sort by `slot.key` alphabetical, no per-column sort.
- Multi-select filter chips — single-select with `All` as default.
- Keyboard nav refinements (arrow keys to traverse rows, ⌘F filter focus, etc.) — accept the SwiftUI defaults; refinements deferred.

## 3. Architecture

### View tree

```
SchedulesView (NavigationSplitView wrapper)
├── SchedulesHeader
│   ├── SchedulesTitle (serif, with live-time subtitle)
│   ├── SchedulesViewToggle (Table | Cards | Timeline-disabled, @SceneStorage)
│   └── + New button (DS.Accent.fill)
├── SchedulesFilterChips (All + per-type, single-select)
├── HSplitView
│   ├── master content (depends on view toggle)
│   │   ├── SchedulesMasterTable
│   │   │   └── SlotTableRow (per slot)
│   │   └── SchedulesMasterCards
│   │       └── SlotCard (per slot, LazyVGrid)
│   └── SchedulesDetailPane
│       ├── SlotEditForm (when slot selected; lifted from Plan 6, restyled)
│       └── SchedulesDetailEmptyState (when none selected)
└── overlay: stale banner + error banner (unchanged from Plan 6)
```

### State ownership

```swift
struct SchedulesView: View {
    @EnvironmentObject var service: ScheduleEditService
    @EnvironmentObject var appState: AppState

    // Persistence: view mode survives app restart.
    @SceneStorage("schedulesView") private var viewMode: SchedulesViewMode = .table

    // Per-tab-mount state; doesn't survive launch.
    @State private var filterMode: SchedulesFilterMode = .all
    @State private var selectedSlotKey: String?
    @State private var newDraftSlot: Slot?

    // Banners + initial-load (lifted unchanged from Plan 6).
    @State private var staleBannerVisible = false
    @State private var stalenessDetail: String?
    @State private var errorMessage: String?
    @State private var isInitialLoading = true
}

enum SchedulesViewMode: String { case table, cards, timeline }
enum SchedulesFilterMode: Equatable {
    case all
    case type(SlotType)
}
```

### Data flow (unchanged from Plan 6)

- `service.slots` published from `ScheduleEditService.loadAll()` via `scoutctl schedule list --json`.
- Filtered list: `displayed = filterMode.apply(to: service.slots)` — pure derivation.
- `selectedSlot` lookup: `service.slots.first { $0.key == selectedSlotKey }`.
- New-draft flow identical to Plan 6: `+ New` inserts `newDraftSlot` and selects it.
- Save/Delete/Fire-now error routing: `StaleScheduleError` → stale banner; other errors → error banner.

## 4. DS extension — Slot type palette

New `DS.SlotType` namespace at the bottom of `Scout/Utilities/DesignSystem.swift`. Light/dark adaptive in the existing `Color(fallbackLight:fallbackDark:)` pattern.

```swift
extension DS {
    /// Slot-type color palette. Distinct from `Accent.fill` (orange `+ New`)
    /// and from `Priority.*` (action-item urgency).
    enum SlotType {
        /// Briefing — warm amber.
        static let briefing      = Color(fallbackLight: .sRGB(0.860, 0.660, 0.180, 1),
                                         fallbackDark:  .sRGB(0.910, 0.760, 0.340, 1))
        /// Consolidation — desaturated steel blue.
        static let consolidation = Color(fallbackLight: .sRGB(0.400, 0.580, 0.760, 1),
                                         fallbackDark:  .sRGB(0.520, 0.700, 0.870, 1))
        /// Dreaming — quiet violet.
        static let dreaming      = Color(fallbackLight: .sRGB(0.560, 0.460, 0.760, 1),
                                         fallbackDark:  .sRGB(0.700, 0.620, 0.880, 1))
        /// Research — sage green.
        static let research      = Color(fallbackLight: .sRGB(0.420, 0.620, 0.420, 1),
                                         fallbackDark:  .sRGB(0.520, 0.740, 0.540, 1))
        /// Manual — matched-chroma neutral; manual slots have no fixed
        /// cadence and shouldn't compete visually.
        static let manual        = Color(fallbackLight: .sRGB(0.620, 0.620, 0.640, 1),
                                         fallbackDark:  .sRGB(0.520, 0.520, 0.535, 1))

        /// Convenience lookup. Used by every cell that renders a type-tinted dot.
        static func color(for type: Scout.SlotType) -> Color {
            switch type {
            case .briefing:      return briefing
            case .consolidation: return consolidation
            case .dreaming:      return dreaming
            case .research:      return research
            case .manual:        return manual
            }
        }
    }
}
```

**Where colors apply:** type-pill dot, day-circle fills, filter-chip dot prefix, card left border. **Where they don't:** `+ New` button (uses `DS.Accent.fill` orange), selection ring on rows (also `DS.Accent.fill`). Slot-type and action-color palettes stay distinct semantic axes.

## 5. Header

### Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  Schedules                          [Table | Cards | …]   [+ New]   │
│  10 active · 4 types · now 16:46                                     │
└──────────────────────────────────────────────────────────────────────┘
```

### Components

- **Title** — `Text("Schedules").font(DS.serif(28, weight: .medium))`. `DS.Ink.p1`.
- **Subtitle** — `<count> active · <typeCount> types · now <HH:MM>`. `DS.sans(12)`, `DS.Ink.p3`. The clock segment refreshes every 60s.
- **View toggle** — segmented `Picker` styled with `.pickerStyle(.segmented)` over `SchedulesViewMode.allCases`. Disabled `.timeline` segment carries a tooltip via `.help("Timeline view arrives in a future plan.")`. Width ~280pt.
- **+ New button** — `Button { addDraftSlot() } label: { ... }` styled with `DS.Accent.fill` background, `DS.Paper.base` foreground, 8pt corner radius, `DS.sans(13, weight: .medium)`.

### Live-time refresh

`SchedulesHeader` owns a `@State private var now: Date = Date()` and a `Timer.publish(every: 60, on: .main, in: .common).autoconnect()` subscriber that updates `now` once a minute. Subtitle reads `now.formatted(date: .omitted, time: .shortened)`. Negligible cost; matches the mockup's "now 16:46" affordance.

## 6. Filter chips

### Layout

```
[All 10]  [● Briefing 2]  [● Consolidation 4]  [● Dreaming 3]  [● Research 1]
```

### Behavior

Single-select. `All` is the default and selected state. Clicking a type chip swaps selection. Counts are derived live from `service.slots`. Manual type only appears as a chip if any slots are of `manual` type — empty types omit their chip rather than showing `0`.

### Visual

- Each chip: pill (24pt height) with horizontal padding.
- Selected chip: `DS.Ink.p1` background, `DS.Paper.base` foreground.
- Unselected chip: `DS.Paper.raised` background, `DS.Ink.p2` foreground.
- Type chips have a 6pt filled circle prefix in `DS.SlotType.color(for: type)`.
- Hover state: `DS.Paper.sunk` background.
- Count text in `DS.mono(11)` slightly desaturated.

### Filter + selection interplay

- When a chip filters out the currently-selected slot, `selectedSlotKey` resets to `nil` → detail pane shows empty state. (Don't try to remember the selection; small re-selection cost is fine.)
- When a draft slot is in flight (`newDraftSlot` non-nil), the draft row remains visible regardless of filter — drafts are uncategorized until first save.

## 7. Master views

### 7.1 Table view (`SchedulesMasterTable`)

Six columns: `NAME · TYPE · TIME · DAYS · ON MISS · COOLDOWN`. Rendered as a custom `LazyVStack` of `SlotTableRow` views with a header row at top — not a SwiftUI `Table` because per-cell custom rendering (day circles, type pills) is awkward with `Table`'s `TableColumn` API.

**Header row:** `DS.sans(10, weight: .medium)`, uppercase, tracking +1pt, in `DS.Ink.p4`. Bottom border `DS.Rule.hard`.

**Slot row (`SlotTableRow`)** layout per cell:

| Cell | Render |
|---|---|
| NAME | `slot.key` in `DS.mono(13)` + 12pt `lock.fill` glyph in `DS.Ink.p4` (forward-compat — Plan 8 may distinguish ad-hoc from canonical slots) |
| TYPE | 6pt circle (`DS.SlotType.color`) + capitalized name in `DS.sans(13)` |
| TIME | `DS.mono(14, weight: .semibold)`, `DS.Ink.p1` |
| DAYS | 7 × 16pt circles labelled M/T/W/T/F/S/S; active filled with `DS.SlotType.color`, inactive `DS.Ink.p4` outline-only. Below the row: derived label in `DS.sans(11)`, `DS.Ink.p3` (`weekdays`, `weekends`, `every day`, `Mon-Wed`, or comma-list fallback) |
| ON MISS | Pill (`DS.sans(11, weight: .medium)`, uppercase). `SKIP` → `DS.Ink.p3` background. `FIRE` → `DS.Status.warn` background. `COLLAPSE` → `DS.Accent.wash` background. |
| COOLDOWN | `DS.mono(13)` value + 8pt `bolt.fill` glyph in `DS.Ink.p4` |

**Row interactions:**
- Hover: `DS.Paper.raised` background.
- Selected: 2pt left accent stripe in `DS.Accent.fill` + `DS.Paper.raised` background.
- Click anywhere in row: select.
- Row height ~52pt (taller than typical macOS rows because of the day-circles row beneath).

### 7.2 Cards view (`SchedulesMasterCards`)

`LazyVGrid` with `GridItem(.adaptive(minimum: 240, maximum: 320))`. 4-up at typical widths, falls back to 1-up at narrow widths.

**Card layout** per mockup #2:

```
┌────────────────────────────┐ ← 4pt left border in DS.SlotType.color
│ 7:00 AM   ● Dreaming       │
│                            │
│ dreaming-weekend-morning   │
│                            │
│ M T W T F S S              │
│ ░ ░ ░ ░ ░ ● ●              │
│ COOLDOWN  120m             │
│                            │
│ SKIP                       │
└────────────────────────────┘
```

- 4pt-wide colored left border via overlay or background gradient.
- **Time:** `DS.serif(28, weight: .medium)`, with small `AM`/`PM` suffix in `DS.sans(11)`, `DS.Ink.p3`.
- **Type pill:** same as table.
- **Slot key:** `DS.mono(12)`, `DS.Ink.p2`.
- **Day circles:** smaller (12pt) than table version. No derived weekdays/weekends label — space-constrained.
- **Cooldown:** `DS.sans(10, weight: .medium)` label `COOLDOWN` (uppercase, tracking +1pt) + `DS.mono(12)` value.
- **ON MISS pill:** same as table, anchored bottom-left.
- Card padding: 16pt all sides.
- Background: `DS.Paper.raised`. Selected: 2pt outline in `DS.Accent.fill`.
- Click anywhere in card: select.

### 7.3 Empty state (both views)

When `displayed.isEmpty && newDraftSlot == nil`, master pane renders a `ContentUnavailableView`:

```
            (calendar.badge.plus icon)

         No scheduled slots
   Add a slot to start scheduling Scout runs.
   Or run scoutctl schedule init to seed
   the plugin defaults (10 standard slots).

           [+ Add slot]
```

Copy and call-to-action lifted unchanged from Plan 6 (works), restyled with DS tokens (`DS.serif(20)` title, `DS.sans(13)` body, button matches `+ New` styling).

## 8. Detail pane

### Layout

```
┌──────────────────────────────────┐
│ morning-briefing                 │  ← title (DS.mono(15))
│ ● Briefing                       │  ← type pill
├──────────────────────────────────┤
│                                  │
│  [SlotEditForm contents]         │
│                                  │
│  Common fields                   │
│  - Time         [HH:MM]          │
│  - Weekdays     [chips]          │
│  - On miss      [picker]         │
│  - Cooldown     [stepper]        │
│                                  │
│  ▾ Advanced                      │
│  - Runner / missed-window / type │
│    / runtime (Plan 8 disabled)   │
│                                  │
├──────────────────────────────────┤
│  Delete   Fire now    Revert Save│  ← action bar (sticky bottom)
└──────────────────────────────────┘
```

### Container

`SchedulesDetailPane` wraps:
- A small selected-slot summary header (slot key in mono + type pill, plus close affordance via clearing selection).
- The `SlotEditForm` body, lifted from Plan 6 unchanged. The form's `init(liveSlot:isNewDraft:onSave:onDelete:onFireNow:onRevertNewDraft:)` signature stays as-is — controller dispatch from Plan 6 is reused.
- Scrollable when content exceeds pane height.

### Empty state (no row selected)

```
            (cursorarrow.click icon)

         Pick a slot to edit
    Click a row in the list to edit
    its time, weekdays, cooldown,
    and other settings.
```

`DS.serif(18)` title, `DS.sans(13)` body, `DS.Ink.p3`. No CTA — selection is the action.

### `SlotEditForm` restyle

The form's internal layout from Plan 6 stays intact. Visual updates:
- Section labels (`Time`, `Weekdays`, `On miss`, `Cooldown`, `Advanced`) restyled in `DS.sans(11, weight: .medium)`, uppercase, tracking +1pt, `DS.Ink.p3`.
- TextField, Stepper, Picker controls stay native SwiftUI (no DS overlay) — consistent with existing Plan 5+6 controls; restyling them is a Plan 9+ task.
- Inline validation errors keep `DS.Status.err` color.
- Save button uses `DS.Accent.fill`. Delete button uses `DS.Status.err`. Revert + Fire-now use `DS.Ink.p2`.
- Keyboard shortcut `⌘↩` for Save preserved.

## 9. Banners

Stale-edit banner + error banner from Plan 6 stay functionally identical, restyled with DS tokens:

- **Stale banner:** `DS.Status.warn` background tinted to ~15% opacity. Icon `exclamationmark.triangle.fill` in `DS.Status.warn`. Title `DS.sans(13, weight: .medium)`. Detail `DS.sans(11)`, `DS.Ink.p3`. `Reload now` button uses `DS.Accent.fill`. `Dismiss` is text-only `DS.Ink.p3`.
- **Error banner:** `DS.Status.err` background tinted to ~12% opacity. Icon `xmark.octagon.fill` in `DS.Status.err`. `Dismiss` text-only.

Banner placement: above the filter chips, below the header. Same as Plan 6 (top of view).

## 10. Migration & file structure

### New files

| File | Purpose |
|---|---|
| `Scout/Schedules/SchedulesHeader.swift` | Title + subtitle + view toggle + + New button |
| `Scout/Schedules/SchedulesViewToggle.swift` | Segmented Table/Cards/Timeline picker |
| `Scout/Schedules/SchedulesFilterChips.swift` | All + per-type chips |
| `Scout/Schedules/SchedulesMasterTable.swift` | Table view container + header row |
| `Scout/Schedules/SlotTableRow.swift` | Single table row |
| `Scout/Schedules/SchedulesMasterCards.swift` | Cards grid |
| `Scout/Schedules/SlotCard.swift` | Single card |
| `Scout/Schedules/SchedulesDetailPane.swift` | Detail-pane wrapper (handles slot/empty/draft state) |
| `Scout/Schedules/SchedulesViewMode.swift` | Enum + persistence helper |
| `Scout/Schedules/SchedulesFilterMode.swift` | Enum + filter logic |
| `Scout/Schedules/DayCircleStrip.swift` | 7-circle weekday strip used by both Table and Cards |
| `Scout/Schedules/TypePill.swift` | Reusable colored-dot + name pill |
| `Scout/Schedules/OnMissPill.swift` | SKIP/FIRE/COLLAPSE pill |
| `Scout/Schedules/WeekdaysFormatter.swift` | Pure helper: `[String] → "weekdays" / "weekends" / "every day" / "Mon-Wed" / "Mon, Wed, Fri"` |

### Modified files

- `Scout/Utilities/DesignSystem.swift` — adds `DS.SlotType` namespace.
- `Scout/Schedules/SchedulesView.swift` — full rewrite to host `NavigationSplitView` with the new components.
- `Scout/Schedules/SlotEditForm.swift` — minor restyle of section labels + button colors. Public init unchanged.
- `Scout/Schedules/SlotRow.swift` — **deleted**. Plan 6's container that switched between summary + expanded form is no longer used; master/detail replaces inline-expand.
- `Scout/Schedules/SlotSummaryRow.swift` — **deleted**. Plan 6's collapsed-row component is replaced by `SlotTableRow` + `SlotCard`.

### New tests

| Test file | Coverage |
|---|---|
| `ScoutTests/Schedules/SchedulesViewToggleTests.swift` | `SchedulesViewMode` rawValue round-trip; default `.table` |
| `ScoutTests/Schedules/SchedulesFilterModeTests.swift` | `apply(to:)` for `.all` (passthrough), `.type(.briefing)` (filters), counts per type |
| `ScoutTests/Schedules/WeekdaysFormatterTests.swift` | All 5 derivation cases (weekdays / weekends / every day / contiguous range / comma list) |
| `ScoutTests/Schedules/TypePillTests.swift` | Snapshot of name + color resolution for each `SlotType` |
| `ScoutTests/Schedules/DayCircleStripTests.swift` | Active vs inactive circles per weekday set |

### Tests deleted

- `ScoutTests/Schedules/SlotSummaryRowTests.swift` — view deleted.

### Tests preserved

All Plan 6 service-layer + form tests stay green:
- `ScoutTests/Models/SlotTests.swift` ✓
- `ScoutTests/Services/ScheduleEditServiceTests.swift` ✓
- `ScoutTests/Schedules/SlotEditFormTests.swift` ✓ (form internals unchanged)
- `ScoutTests/Schedules/SchedulesViewTests.swift` — needs minor update to reference `SchedulesView`'s new `nextNewSlotKey` / `makeNewDraftSlot` static helpers (which are preserved verbatim).
- `ScoutTests/Integration/ScheduleEditE2ETest.swift` ✓ (opt-in, no view dependencies).

## 11. Rollout

**No manifest flag.** Plan 5/6 features are unchanged at the engine boundary; this is a UI-only rewrite.

**Branch + PR strategy:** single scout-app branch `plan-7-schedules-visual`. No engine changes. Vault unchanged.

**Order of work (single PR):**
1. Add `DS.SlotType` namespace + tests for `color(for:)` resolution.
2. Add formatters + pure helpers (`WeekdaysFormatter`, `SchedulesFilterMode`, `SchedulesViewMode`).
3. Add reusable presentational components (`TypePill`, `OnMissPill`, `DayCircleStrip`).
4. Add `SlotTableRow` + `SlotCard`.
5. Add `SchedulesMasterTable` + `SchedulesMasterCards`.
6. Add `SchedulesHeader` + `SchedulesViewToggle` + `SchedulesFilterChips`.
7. Add `SchedulesDetailPane` (wraps `SlotEditForm` + empty-state).
8. Rewrite `SchedulesView` to wire everything via `NavigationSplitView`.
9. Restyle `SlotEditForm` (section labels + button colors).
10. Delete `SlotRow.swift` + `SlotSummaryRow.swift` + their tests.
11. Build + smoke-launch + ship.

## 12. Risks

**`NavigationSplitView` adaptive behavior at narrow widths.** macOS's `NavigationSplitView` collapses to a single column with push navigation when the window is narrow. The detail pane becomes a pushed view. Acceptable — matches macOS conventions — but the initial selection state matters: at app launch with no row selected, the master shows full-width and detail is hidden. Verify this works without flicker.

**Live time refresh re-rendering the whole tree.** `Timer.publish(every: 60)` updates `now` in the header's `@State`. SwiftUI dependency tracking should isolate the redraw to `SchedulesHeader`. If profiling shows a wider invalidation, scope the timer to a child view.

**Color contrast on dark mode.** The `DS.SlotType` palette's dark variants need to read clearly on `DS.Paper.base` dark. The proposed values are tested visually against the existing DS dark-mode surfaces (Control Center) but real-render verification on the live app is needed before merge.

**Filter-chip layout overflow.** With `manual` slots present, 5 chips + 1 `All` may overflow narrow windows. Use a `LazyHStack` inside a horizontal `ScrollView` so chips wrap or scroll rather than truncate.

**`DS.SlotType.color(for:)` Swift namespace clash.** The current `Slot.swift` declares `enum SlotType` at file scope. The new `DS.SlotType` enum lives inside `DS`. The conditional in `color(for: Scout.SlotType)` requires fully-qualifying as `Scout.SlotType` to disambiguate. Spec'd; double-check at write time.

## 13. Decision log

| # | Question | Decision | Alternatives rejected |
|---|---|---|---|
| 1 | View-mode scope | Table + Cards (toggle); Timeline disabled chip | Table only; All three with Timeline scoped + designed |
| 2 | Edit access pattern | Master/detail (`NavigationSplitView` w/ side detail pane) | Inline-expand (Plan 6 default); modal sheet |
| 3 | Sidebar redesign | Out of scope; current sidebar stays | Build DAYS + FILTERS sections from mockup |
| 4 | + New flow in master/detail | Insert draft row in master + auto-select | Detail-pane-only "create mode" with no master row |
| 5 | App-wide DS unification | Schedules now; Settings as small followup PR | Big-bang unification plan covering all surfaces |
| 6 | Type-color palette | New `DS.SlotType` namespace (briefing/consolidation/dreaming/research/manual) | Reuse `DS.Priority.*`; ship without per-type colors |
| 7 | Filter chip behavior | Single-select with `All` default; counts derived live | Multi-select; persist across launches |
| 8 | View-mode persistence | `@SceneStorage` (survives app restart) | Per-tab-mount only |
| 9 | Live time in subtitle | `Timer.publish(every: 60)`-driven; refreshes header view only | Static (computed once at mount); 1Hz tick |
| 10 | Manual slot type chip | Hidden when count is 0 | Always shown with `0` count |
| 11 | Sortable columns | Single sort by `slot.key` alphabetical | Click-to-sort per column |
| 12 | TextField/Stepper/Picker DS overlay | Native SwiftUI controls (consistent with Plan 5/6); DS overlay deferred | Custom-styled controls in this plan |
