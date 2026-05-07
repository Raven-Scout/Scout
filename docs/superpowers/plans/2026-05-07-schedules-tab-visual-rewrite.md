# Schedules Tab Visual Rewrite Implementation Plan (Plan 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the Schedules tab to match the editorial design language used by Control Center + Action Items — DS-aligned typography + colors, master/detail edit pattern, view toggle with Table + Cards layouts, type-color palette, filter chips, live header.

**Architecture:** UI-only rewrite on top of the Plan 6 service layer. `ScheduleEditService` + `Slot` model + `SlotDraft` + `SlotEditForm` (lightly restyled) are reused. New presentational components (`TypePill`, `OnMissPill`, `DayCircleStrip`) compose into `SlotTableRow` + `SlotCard`. Master views (`SchedulesMasterTable` + `SchedulesMasterCards`) flow from a top-level `SchedulesView` rewrite that hosts a `NavigationSplitView`. Plan 6's `SlotRow` + `SlotSummaryRow` + their tests are deleted (replaced by master/detail pattern).

**Tech Stack:** Swift 5.9 + SwiftUI + scout-app's existing `DS` design system (`DS.Paper`, `DS.Ink`, `DS.Rule`, `DS.Accent`, `DS.serif/mono/sans`). Swift Testing for new tests. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-05-07-schedules-tab-visual-rewrite-design.md` (commit `f234bcb`).

---

## File Structure

### Modified files

| File | Responsibility | Action |
|---|---|---|
| `Scout/Utilities/DesignSystem.swift` | `DS` design system — adds `DS.SlotType` namespace at end | Modify |
| `Scout/Schedules/SchedulesView.swift` | Top-level — rewrite around `NavigationSplitView` | Rewrite |
| `Scout/Schedules/SlotEditForm.swift` | Lightly restyle section labels + button colors | Modify |

### New files

| File | Responsibility |
|---|---|
| `Scout/Schedules/WeekdaysFormatter.swift` | Pure: `[String] → "weekdays" / "weekends" / "every day" / "Mon-Wed" / "Mon, Wed, Fri"` |
| `Scout/Schedules/SchedulesViewMode.swift` | Enum `case table, cards, timeline` + `@SceneStorage`-friendly raw values |
| `Scout/Schedules/SchedulesFilterMode.swift` | Enum + `apply(to:)` filter + per-type counts |
| `Scout/Schedules/TypePill.swift` | Reusable colored-dot + capitalized name pill |
| `Scout/Schedules/OnMissPill.swift` | SKIP/FIRE/COLLAPSE pill with status-color tinting |
| `Scout/Schedules/DayCircleStrip.swift` | 7-circle weekday strip with active fill in slot type color |
| `Scout/Schedules/SlotTableRow.swift` | One slot rendered as a table row (6-column NAME/TYPE/TIME/DAYS/ON-MISS/COOLDOWN) |
| `Scout/Schedules/SlotCard.swift` | One slot rendered as a card |
| `Scout/Schedules/SchedulesMasterTable.swift` | Container — header row + LazyVStack of `SlotTableRow` |
| `Scout/Schedules/SchedulesMasterCards.swift` | Container — LazyVGrid of `SlotCard` |
| `Scout/Schedules/SchedulesHeader.swift` | Title + subtitle + view toggle + + New button + live-time refresh |
| `Scout/Schedules/SchedulesFilterChips.swift` | All + per-type chips with derived counts |
| `Scout/Schedules/SchedulesDetailPane.swift` | Wraps `SlotEditForm` for selected slot; renders empty state when none selected |

### Deleted files

| File | Reason |
|---|---|
| `Scout/Schedules/SlotRow.swift` | Master/detail replaces inline-expand; this container is no longer used |
| `Scout/Schedules/SlotSummaryRow.swift` | Replaced by `SlotTableRow` + `SlotCard` |
| `ScoutTests/Schedules/SlotSummaryRowTests.swift` | View deleted |

### New tests

| Test file | Coverage |
|---|---|
| `ScoutTests/Schedules/DSSlotTypeTests.swift` | `DS.SlotType.color(for:)` returns the right color per `SlotType` |
| `ScoutTests/Schedules/WeekdaysFormatterTests.swift` | All 5 derivation cases |
| `ScoutTests/Schedules/SchedulesViewModeTests.swift` | RawValue round-trip, default `.table` |
| `ScoutTests/Schedules/SchedulesFilterModeTests.swift` | `apply(to:)` happy paths, type counts |

### Preserved tests (verify green after each task)

`SlotTests`, `ScheduleEditServiceTests`, `SlotEditFormTests`, `SchedulesViewTests`, `ScheduleServiceTests` — all from Plan 5/6.

---

## Task 1: `DS.SlotType` namespace + color resolution test

**Files:**
- Modify: `Scout/Utilities/DesignSystem.swift` (append namespace)
- Create: `ScoutTests/Schedules/DSSlotTypeTests.swift`

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/Schedules/DSSlotTypeTests.swift`:

```swift
import Testing
import SwiftUI
@testable import Scout

@Suite("DS.SlotType")
struct DSSlotTypeTests {

    @Test("color(for:) returns the namespace constant for each SlotType")
    @MainActor
    func test_color_for_each_slot_type() {
        // Equatable on Color compares wrapped resolved Color across light/dark.
        #expect(DS.SlotType.color(for: .briefing)      == DS.SlotType.briefing)
        #expect(DS.SlotType.color(for: .consolidation) == DS.SlotType.consolidation)
        #expect(DS.SlotType.color(for: .dreaming)      == DS.SlotType.dreaming)
        #expect(DS.SlotType.color(for: .research)      == DS.SlotType.research)
        #expect(DS.SlotType.color(for: .manual)        == DS.SlotType.manual)
    }

    @Test("All 5 SlotType cases have distinct colors")
    @MainActor
    func test_all_colors_distinct() {
        let all: [Color] = [
            DS.SlotType.briefing,
            DS.SlotType.consolidation,
            DS.SlotType.dreaming,
            DS.SlotType.research,
            DS.SlotType.manual,
        ]
        // Set semantics — all distinct.
        let unique = Set(all.map { String(describing: $0) })
        #expect(unique.count == all.count)
    }
}
```

- [ ] **Step 2: Run test — expect BUILD FAILED (`DS.SlotType` doesn't exist)**

```bash
cd /Users/jordanburger/scout-app
xcodebuild test -only-testing:ScoutTests/DSSlotTypeTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```

- [ ] **Step 3: Append `DS.SlotType` namespace to `Scout/Utilities/DesignSystem.swift`**

Add at the very end of the file (just before the final closing `}` of `enum DS`, OR as an extension after — find what fits):

```swift
extension DS {
    /// Slot-type color palette. Distinct from `DS.Accent.fill` (orange `+ New`)
    /// and from `DS.Priority.*` (action-item urgency axis).
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
        /// cadence and shouldn't compete visually with the four colored types.
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

(`Scout.SlotType` fully-qualified to disambiguate from the new `DS.SlotType` namespace.)

- [ ] **Step 4: Run test — expect 2 passing**

```bash
xcodebuild test -only-testing:ScoutTests/DSSlotTypeTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```

- [ ] **Step 5: Build whole app**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -5
```
Expected: BUILD SUCCEEDED.

- [ ] **Step 6: Commit**

```bash
cd /Users/jordanburger/scout-app
git add Scout/Utilities/DesignSystem.swift ScoutTests/Schedules/DSSlotTypeTests.swift
git commit -m "feat(app): DS.SlotType color palette (briefing/consolidation/dreaming/research/manual)"
```

---

## Task 2: `WeekdaysFormatter` pure helper

**Files:**
- Create: `Scout/Schedules/WeekdaysFormatter.swift`
- Create: `ScoutTests/Schedules/WeekdaysFormatterTests.swift`

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Schedules/WeekdaysFormatterTests.swift`:

```swift
import Testing
@testable import Scout

@Suite("WeekdaysFormatter")
struct WeekdaysFormatterTests {

    @Test("Mon-Fri yields 'weekdays'")
    func test_weekdays() {
        #expect(WeekdaysFormatter.label(for: ["Mon", "Tue", "Wed", "Thu", "Fri"]) == "weekdays")
    }

    @Test("Sat+Sun yields 'weekends'")
    func test_weekends() {
        #expect(WeekdaysFormatter.label(for: ["Sat", "Sun"]) == "weekends")
    }

    @Test("All 7 yields 'every day'")
    func test_every_day() {
        #expect(WeekdaysFormatter.label(for: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) == "every day")
    }

    @Test("Contiguous range yields 'Mon-Wed' style")
    func test_contiguous_range() {
        #expect(WeekdaysFormatter.label(for: ["Mon", "Tue", "Wed"]) == "Mon-Wed")
        #expect(WeekdaysFormatter.label(for: ["Wed", "Thu", "Fri"]) == "Wed-Fri")
    }

    @Test("Non-contiguous yields comma-list")
    func test_non_contiguous() {
        #expect(WeekdaysFormatter.label(for: ["Mon", "Wed", "Fri"]) == "Mon, Wed, Fri")
    }

    @Test("Single day yields the day name")
    func test_single_day() {
        #expect(WeekdaysFormatter.label(for: ["Tue"]) == "Tue")
    }

    @Test("Empty input yields empty string")
    func test_empty() {
        #expect(WeekdaysFormatter.label(for: []) == "")
    }

    @Test("Order-independent — Sat before Mon still detects weekend pair")
    func test_order_independent() {
        #expect(WeekdaysFormatter.label(for: ["Sun", "Sat"]) == "weekends")
        #expect(WeekdaysFormatter.label(for: ["Fri", "Mon", "Wed", "Tue", "Thu"]) == "weekdays")
    }
}
```

- [ ] **Step 2: Run tests — expect all 8 fail**

```bash
xcodebuild test -only-testing:ScoutTests/WeekdaysFormatterTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```

- [ ] **Step 3: Create `Scout/Schedules/WeekdaysFormatter.swift`**

```swift
import Foundation

/// Pure helper that turns a slot's `weekdays` array into a human-readable label
/// shown beneath the day-circle strip in the Schedules table view.
///
/// Resolution order (first match wins):
///   - Empty → "" (caller handles)
///   - All 7 → "every day"
///   - Mon–Fri → "weekdays"
///   - Sat+Sun → "weekends"
///   - Single day → day name
///   - Contiguous block → "Mon-Wed" style
///   - Otherwise → comma-list, in canonical Mon→Sun order
enum WeekdaysFormatter {

    private static let canonical = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    private static let weekdays = Set(["Mon", "Tue", "Wed", "Thu", "Fri"])
    private static let weekends = Set(["Sat", "Sun"])

    static func label(for days: [String]) -> String {
        let set = Set(days)
        guard !set.isEmpty else { return "" }
        if set.count == 7                       { return "every day" }
        if set == weekdays                      { return "weekdays" }
        if set == weekends                      { return "weekends" }

        // Re-sort into canonical order before deciding contiguous-vs-list.
        let sorted = canonical.filter { set.contains($0) }

        if sorted.count == 1                    { return sorted[0] }
        if isContiguous(sorted)                 { return "\(sorted.first!)-\(sorted.last!)" }
        return sorted.joined(separator: ", ")
    }

    /// True when the input is a contiguous slice of the canonical Mon→Sun order.
    private static func isContiguous(_ sorted: [String]) -> Bool {
        guard let first = sorted.first, let firstIdx = canonical.firstIndex(of: first) else {
            return false
        }
        for (offset, day) in sorted.enumerated() {
            let idx = firstIdx + offset
            guard idx < canonical.count, canonical[idx] == day else { return false }
        }
        return true
    }
}
```

- [ ] **Step 4: Run tests — expect 8 passing**

```bash
xcodebuild test -only-testing:ScoutTests/WeekdaysFormatterTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```

- [ ] **Step 5: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add Scout/Schedules/WeekdaysFormatter.swift ScoutTests/Schedules/WeekdaysFormatterTests.swift
git commit -m "feat(app): WeekdaysFormatter — derive 'weekdays'/'weekends'/'every day'/'Mon-Wed'/list"
```

---

## Task 3: `SchedulesViewMode` + `SchedulesFilterMode` enums

**Files:**
- Create: `Scout/Schedules/SchedulesViewMode.swift`
- Create: `Scout/Schedules/SchedulesFilterMode.swift`
- Create: `ScoutTests/Schedules/SchedulesViewModeTests.swift`
- Create: `ScoutTests/Schedules/SchedulesFilterModeTests.swift`

- [ ] **Step 1: Write tests**

`ScoutTests/Schedules/SchedulesViewModeTests.swift`:

```swift
import Testing
@testable import Scout

@Suite("SchedulesViewMode")
struct SchedulesViewModeTests {
    @Test("rawValue round-trip")
    func test_raw_value_round_trip() {
        for mode in SchedulesViewMode.allCases {
            #expect(SchedulesViewMode(rawValue: mode.rawValue) == mode)
        }
    }

    @Test("default is .table")
    func test_default_is_table() {
        #expect(SchedulesViewMode.default == .table)
    }

    @Test("allCases includes all three modes")
    func test_all_cases() {
        #expect(SchedulesViewMode.allCases.count == 3)
        #expect(SchedulesViewMode.allCases.contains(.table))
        #expect(SchedulesViewMode.allCases.contains(.cards))
        #expect(SchedulesViewMode.allCases.contains(.timeline))
    }

    @Test("isAvailable — table and cards are available, timeline is not")
    func test_is_available() {
        #expect(SchedulesViewMode.table.isAvailable == true)
        #expect(SchedulesViewMode.cards.isAvailable == true)
        #expect(SchedulesViewMode.timeline.isAvailable == false)
    }
}
```

`ScoutTests/Schedules/SchedulesFilterModeTests.swift`:

```swift
import Testing
@testable import Scout

@Suite("SchedulesFilterMode")
struct SchedulesFilterModeTests {

    static func slot(_ key: String, type: SlotType) -> Slot {
        Slot(
            key: key,
            type: type,
            runner: "run-scout.sh",
            firesAtLocal: "08:00",
            weekdays: ["Mon"],
            missedWindowHours: 4,
            onMiss: .fire,
            cooldownMinutes: 60
        )
    }

    static let mixed: [Slot] = [
        slot("morning-briefing", type: .briefing),
        slot("morning-consolidation", type: .consolidation),
        slot("midday-consolidation", type: .consolidation),
        slot("dreaming-evening", type: .dreaming),
        slot("research", type: .research),
    ]

    @Test(".all is passthrough")
    func test_all_passthrough() {
        let filtered = SchedulesFilterMode.all.apply(to: Self.mixed)
        #expect(filtered.count == Self.mixed.count)
        #expect(filtered.map(\.key) == Self.mixed.map(\.key))
    }

    @Test(".type filters to that type only")
    func test_type_filter() {
        let consolidations = SchedulesFilterMode.type(.consolidation).apply(to: Self.mixed)
        #expect(consolidations.count == 2)
        #expect(consolidations.allSatisfy { $0.type == .consolidation })
    }

    @Test("count returns correct count per type")
    func test_count_per_type() {
        #expect(SchedulesFilterMode.count(of: .briefing,      in: Self.mixed) == 1)
        #expect(SchedulesFilterMode.count(of: .consolidation, in: Self.mixed) == 2)
        #expect(SchedulesFilterMode.count(of: .dreaming,      in: Self.mixed) == 1)
        #expect(SchedulesFilterMode.count(of: .research,      in: Self.mixed) == 1)
        #expect(SchedulesFilterMode.count(of: .manual,        in: Self.mixed) == 0)
    }
}
```

- [ ] **Step 2: Run tests — expect BUILD FAILED**

```bash
xcodebuild test -only-testing:ScoutTests/SchedulesViewModeTests -only-testing:ScoutTests/SchedulesFilterModeTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```

- [ ] **Step 3: Create `Scout/Schedules/SchedulesViewMode.swift`**

```swift
import Foundation

/// View mode for the Schedules tab — Table (default), Cards, or Timeline.
/// Persists across app launches via `@SceneStorage("schedulesView")`.
enum SchedulesViewMode: String, CaseIterable, Identifiable, Hashable {
    case table
    case cards
    case timeline

    var id: String { rawValue }

    /// The default view when no scene-storage value exists.
    static let `default`: SchedulesViewMode = .table

    /// Timeline is reserved for a future plan; segments render as disabled
    /// in the picker but still present visually so the toggle's full shape
    /// is visible.
    var isAvailable: Bool {
        switch self {
        case .table, .cards: return true
        case .timeline:      return false
        }
    }

    /// Display label for the segmented picker.
    var displayName: String {
        switch self {
        case .table:    return "Table"
        case .cards:    return "Cards"
        case .timeline: return "Timeline"
        }
    }
}
```

- [ ] **Step 4: Create `Scout/Schedules/SchedulesFilterMode.swift`**

```swift
import Foundation

/// Filter state for the Schedules tab. `.all` is the default; `.type(...)`
/// filters the master list to a single slot type.
enum SchedulesFilterMode: Hashable {
    case all
    case type(SlotType)

    /// Apply the filter to a slot list. Pure; no allocation when `.all`.
    func apply(to slots: [Slot]) -> [Slot] {
        switch self {
        case .all:
            return slots
        case .type(let target):
            return slots.filter { $0.type == target }
        }
    }

    /// Count slots of the given type in the source list. Used by the
    /// per-type filter chips' badge counts (so empty types can hide their
    /// chip rather than render `0`).
    static func count(of type: SlotType, in slots: [Slot]) -> Int {
        slots.lazy.filter { $0.type == type }.count
    }
}
```

- [ ] **Step 5: Run tests — expect 7 passing**

```bash
xcodebuild test -only-testing:ScoutTests/SchedulesViewModeTests -only-testing:ScoutTests/SchedulesFilterModeTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```

- [ ] **Step 6: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 7: Commit**

```bash
git add Scout/Schedules/SchedulesViewMode.swift Scout/Schedules/SchedulesFilterMode.swift \
        ScoutTests/Schedules/SchedulesViewModeTests.swift ScoutTests/Schedules/SchedulesFilterModeTests.swift
git commit -m "feat(app): SchedulesViewMode + SchedulesFilterMode enums for tab state"
```

---

## Task 4: `TypePill` presentational component

**Files:**
- Create: `Scout/Schedules/TypePill.swift`

(No new tests — pure presentational; logic is `DS.SlotType.color(for:)` + `type.rawValue.capitalized`, both already covered.)

- [ ] **Step 1: Create `Scout/Schedules/TypePill.swift`**

```swift
import SwiftUI

/// Compact slot-type indicator: 6pt filled circle in the slot's type color
/// + capitalized type name. Used in table rows, cards, filter chips, and
/// the detail-pane header.
struct TypePill: View {
    let type: SlotType

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(DS.SlotType.color(for: type))
                .frame(width: 6, height: 6)
            Text(type.rawValue.capitalized)
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p2)
        }
    }
}
```

- [ ] **Step 2: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```
Expected: BUILD SUCCEEDED.

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/TypePill.swift
git commit -m "feat(app): TypePill — colored dot + type name presentational component"
```

---

## Task 5: `OnMissPill` presentational component

**Files:**
- Create: `Scout/Schedules/OnMissPill.swift`

- [ ] **Step 1: Create `Scout/Schedules/OnMissPill.swift`**

```swift
import SwiftUI

/// On-miss policy badge. SKIP / FIRE / COLLAPSE in uppercase, color-tinted
/// per policy:
///   - SKIP     → DS.Ink.p3 background (quiet)
///   - FIRE     → DS.Status.warn background (active)
///   - COLLAPSE → DS.Accent.wash background (deferred)
struct OnMissPill: View {
    let policy: OnMissPolicy

    var body: some View {
        Text(policy.rawValue.uppercased())
            .font(DS.sans(11, weight: .medium))
            .foregroundStyle(foreground)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(background, in: Capsule())
            .accessibilityLabel("On miss: \(policy.rawValue)")
    }

    private var background: Color {
        switch policy {
        case .skip:     return DS.Ink.p4.opacity(0.18)
        case .fire:     return DS.Status.warn.opacity(0.20)
        case .collapse: return DS.Accent.wash
        }
    }

    private var foreground: Color {
        switch policy {
        case .skip:     return DS.Ink.p2
        case .fire:     return DS.Status.warn
        case .collapse: return DS.Accent.ink
        }
    }
}
```

- [ ] **Step 2: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/OnMissPill.swift
git commit -m "feat(app): OnMissPill — SKIP/FIRE/COLLAPSE policy badge with status tinting"
```

---

## Task 6: `DayCircleStrip` presentational component

**Files:**
- Create: `Scout/Schedules/DayCircleStrip.swift`

- [ ] **Step 1: Create `Scout/Schedules/DayCircleStrip.swift`**

```swift
import SwiftUI

/// 7-circle weekday strip: M T W T F S S. Each circle is filled in the
/// slot's type color when the day is active; otherwise drawn as an outline
/// in DS.Ink.p4. Used by SlotTableRow (16pt) and SlotCard (12pt).
struct DayCircleStrip: View {
    let activeDays: Set<String>
    let typeColor: Color
    let diameter: CGFloat

    private static let order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    var body: some View {
        HStack(spacing: 4) {
            ForEach(Self.order, id: \.self) { day in
                circle(for: day)
                    .accessibilityLabel(day)
                    .accessibilityValue(activeDays.contains(day) ? "active" : "inactive")
            }
        }
    }

    @ViewBuilder
    private func circle(for day: String) -> some View {
        let active = activeDays.contains(day)
        ZStack {
            if active {
                Circle().fill(typeColor)
            } else {
                Circle().stroke(DS.Ink.p4, lineWidth: 1)
            }
            Text(letterFor(day))
                .font(DS.sans(max(8, diameter * 0.55), weight: .medium))
                .foregroundStyle(active ? DS.Paper.base : DS.Ink.p3)
        }
        .frame(width: diameter, height: diameter)
    }

    private func letterFor(_ day: String) -> String {
        switch day {
        case "Mon": return "M"
        case "Tue": return "T"
        case "Wed": return "W"
        case "Thu": return "T"
        case "Fri": return "F"
        case "Sat": return "S"
        case "Sun": return "S"
        default:    return String(day.prefix(1))
        }
    }
}
```

- [ ] **Step 2: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/DayCircleStrip.swift
git commit -m "feat(app): DayCircleStrip — 7-circle weekday strip with type-color active fill"
```

---

## Task 7: `SlotTableRow`

**Files:**
- Create: `Scout/Schedules/SlotTableRow.swift`

- [ ] **Step 1: Create `Scout/Schedules/SlotTableRow.swift`**

```swift
import SwiftUI

/// One row in `SchedulesMasterTable`. Six "columns": NAME / TYPE / TIME /
/// DAYS / ON MISS / COOLDOWN. Selection state is owned by the parent;
/// we just render isSelected styling.
struct SlotTableRow: View {
    let slot: Slot
    let isSelected: Bool

    var body: some View {
        HStack(spacing: 16) {
            nameCell.frame(maxWidth: .infinity, alignment: .leading)
            typeCell.frame(width: 140, alignment: .leading)
            timeCell.frame(width: 70, alignment: .leading)
            daysCell.frame(width: 200, alignment: .leading)
            onMissCell.frame(width: 90, alignment: .leading)
            cooldownCell.frame(width: 90, alignment: .leading)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 16)
        .background(rowBackground)
        .overlay(alignment: .leading) {
            if isSelected {
                Rectangle()
                    .fill(DS.Accent.fill)
                    .frame(width: 2)
            }
        }
    }

    @ViewBuilder
    private var rowBackground: some View {
        if isSelected {
            DS.Paper.raised
        } else {
            Color.clear
        }
    }

    private var nameCell: some View {
        HStack(spacing: 6) {
            Text(slot.key)
                .font(DS.mono(13))
                .foregroundStyle(DS.Ink.p1)
            Image(systemName: "lock.fill")
                .font(.system(size: 10))
                .foregroundStyle(DS.Ink.p4)
        }
    }

    private var typeCell: some View {
        TypePill(type: slot.type)
    }

    private var timeCell: some View {
        Text(slot.firesAtLocal)
            .font(DS.mono(14, weight: .semibold))
            .foregroundStyle(DS.Ink.p1)
    }

    private var daysCell: some View {
        VStack(alignment: .leading, spacing: 4) {
            DayCircleStrip(
                activeDays: Set(slot.weekdays),
                typeColor: DS.SlotType.color(for: slot.type),
                diameter: 16
            )
            Text(WeekdaysFormatter.label(for: slot.weekdays))
                .font(DS.sans(11))
                .foregroundStyle(DS.Ink.p3)
        }
    }

    private var onMissCell: some View {
        OnMissPill(policy: slot.onMiss)
    }

    private var cooldownCell: some View {
        HStack(spacing: 4) {
            Text("\(slot.cooldownMinutes)m")
                .font(DS.mono(13))
                .foregroundStyle(DS.Ink.p2)
            Image(systemName: "bolt.fill")
                .font(.system(size: 8))
                .foregroundStyle(DS.Ink.p4)
        }
    }
}
```

- [ ] **Step 2: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/SlotTableRow.swift
git commit -m "feat(app): SlotTableRow — 6-cell row with NAME/TYPE/TIME/DAYS/ON-MISS/COOLDOWN"
```

---

## Task 8: `SlotCard`

**Files:**
- Create: `Scout/Schedules/SlotCard.swift`

- [ ] **Step 1: Create `Scout/Schedules/SlotCard.swift`**

```swift
import SwiftUI

/// One slot rendered as a card in `SchedulesMasterCards`. 4pt left border
/// in slot type color; serif time at top; type pill, slot key, day strip,
/// cooldown, and on-miss policy below.
struct SlotCard: View {
    let slot: Slot
    let isSelected: Bool

    private var typeColor: Color { DS.SlotType.color(for: slot.type) }

    var body: some View {
        HStack(spacing: 0) {
            Rectangle()
                .fill(typeColor)
                .frame(width: 4)
            VStack(alignment: .leading, spacing: 12) {
                topRow
                Text(slot.key)
                    .font(DS.mono(12))
                    .foregroundStyle(DS.Ink.p2)
                DayCircleStrip(
                    activeDays: Set(slot.weekdays),
                    typeColor: typeColor,
                    diameter: 12
                )
                cooldownRow
                OnMissPill(policy: slot.onMiss)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
        }
        .background(DS.Paper.raised, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(isSelected ? DS.Accent.fill : Color.clear, lineWidth: 2)
        )
    }

    private var topRow: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(formattedTime)
                .font(DS.serif(28, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
            Text(amPm)
                .font(DS.sans(11))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
            TypePill(type: slot.type)
        }
    }

    private var cooldownRow: some View {
        HStack(spacing: 6) {
            Text("COOLDOWN")
                .font(DS.sans(10, weight: .medium))
                .tracking(1)
                .foregroundStyle(DS.Ink.p4)
            Text("\(slot.cooldownMinutes)m")
                .font(DS.mono(12))
                .foregroundStyle(DS.Ink.p2)
        }
    }

    /// Convert "HH:MM" 24-hour to "H:MM" 12-hour for the big card display.
    private var formattedTime: String {
        let parts = slot.firesAtLocal.split(separator: ":")
        guard parts.count == 2,
              let h24 = Int(parts[0]), let m = Int(parts[1])
        else { return slot.firesAtLocal }
        let h12 = h24 == 0 ? 12 : (h24 > 12 ? h24 - 12 : h24)
        return String(format: "%d:%02d", h12, m)
    }

    private var amPm: String {
        let parts = slot.firesAtLocal.split(separator: ":")
        guard let h24 = parts.first.flatMap({ Int($0) }) else { return "" }
        return h24 < 12 ? "AM" : "PM"
    }
}
```

- [ ] **Step 2: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/SlotCard.swift
git commit -m "feat(app): SlotCard — colored-border card with serif time + type/days/cooldown/on-miss"
```

---

## Task 9: `SchedulesMasterTable` container

**Files:**
- Create: `Scout/Schedules/SchedulesMasterTable.swift`

- [ ] **Step 1: Create `Scout/Schedules/SchedulesMasterTable.swift`**

```swift
import SwiftUI

/// Container for the Table view. Header row + LazyVStack of `SlotTableRow`.
/// The parent (`SchedulesView`) supplies the filtered slot list, the
/// optional new-draft slot at the top, and the selection binding.
struct SchedulesMasterTable: View {
    let slots: [Slot]
    let newDraftSlot: Slot?
    @Binding var selectedSlotKey: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            headerRow
            Divider().background(DS.Rule.hard)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if let draft = newDraftSlot {
                        row(for: draft, isDraft: true)
                        Divider().background(DS.Rule.soft)
                    }
                    ForEach(slots) { slot in
                        row(for: slot, isDraft: false)
                        Divider().background(DS.Rule.soft)
                    }
                }
            }
        }
    }

    private var headerRow: some View {
        HStack(spacing: 16) {
            headerCell("NAME").frame(maxWidth: .infinity, alignment: .leading)
            headerCell("TYPE").frame(width: 140, alignment: .leading)
            headerCell("TIME").frame(width: 70, alignment: .leading)
            headerCell("DAYS").frame(width: 200, alignment: .leading)
            headerCell("ON MISS").frame(width: 90, alignment: .leading)
            headerCell("COOLDOWN").frame(width: 90, alignment: .leading)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 16)
    }

    private func headerCell(_ text: String) -> some View {
        Text(text)
            .font(DS.sans(10, weight: .medium))
            .tracking(1)
            .foregroundStyle(DS.Ink.p4)
    }

    @ViewBuilder
    private func row(for slot: Slot, isDraft: Bool) -> some View {
        let isSelected = selectedSlotKey == slot.key
        Button {
            selectedSlotKey = slot.key
        } label: {
            SlotTableRow(slot: slot, isSelected: isSelected)
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            // Hover-tracking is a SwiftUI nicety; row background is handled
            // inline in SlotTableRow's selected state.
            _ = hovering
        }
    }
}
```

- [ ] **Step 2: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/SchedulesMasterTable.swift
git commit -m "feat(app): SchedulesMasterTable — header row + LazyVStack of slot rows"
```

---

## Task 10: `SchedulesMasterCards` container

**Files:**
- Create: `Scout/Schedules/SchedulesMasterCards.swift`

- [ ] **Step 1: Create `Scout/Schedules/SchedulesMasterCards.swift`**

```swift
import SwiftUI

/// Container for the Cards view. LazyVGrid of `SlotCard`. Adaptive columns
/// (240–320pt) flow 4-up at typical widths, 1-up at narrow.
struct SchedulesMasterCards: View {
    let slots: [Slot]
    let newDraftSlot: Slot?
    @Binding var selectedSlotKey: String?

    private let columns = [
        GridItem(.adaptive(minimum: 240, maximum: 320), spacing: 12)
    ]

    var body: some View {
        ScrollView {
            LazyVGrid(columns: columns, alignment: .leading, spacing: 12) {
                if let draft = newDraftSlot {
                    cardButton(for: draft)
                }
                ForEach(slots) { slot in
                    cardButton(for: slot)
                }
            }
            .padding(16)
        }
    }

    @ViewBuilder
    private func cardButton(for slot: Slot) -> some View {
        let isSelected = selectedSlotKey == slot.key
        Button {
            selectedSlotKey = slot.key
        } label: {
            SlotCard(slot: slot, isSelected: isSelected)
        }
        .buttonStyle(.plain)
    }
}
```

- [ ] **Step 2: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/SchedulesMasterCards.swift
git commit -m "feat(app): SchedulesMasterCards — adaptive LazyVGrid of slot cards"
```

---

## Task 11: `SchedulesHeader`

**Files:**
- Create: `Scout/Schedules/SchedulesHeader.swift`

- [ ] **Step 1: Create `Scout/Schedules/SchedulesHeader.swift`**

```swift
import SwiftUI

/// Header bar for the Schedules tab. Serif title, live subtitle (count
/// active · type count · current time), view toggle (Table/Cards/Timeline),
/// and the orange + New button.
struct SchedulesHeader: View {
    let slotCount: Int
    let typeCount: Int
    @Binding var viewMode: SchedulesViewMode
    let onAddSlot: () -> Void

    @State private var now: Date = Date()
    private let timer = Timer.publish(every: 60, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Schedules")
                    .font(DS.serif(28, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Text(subtitle)
                    .font(DS.sans(12))
                    .foregroundStyle(DS.Ink.p3)
            }
            Spacer()
            viewToggle
            addNewButton
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .onReceive(timer) { now = $0 }
    }

    private var subtitle: String {
        "\(slotCount) active · \(typeCount) types · now \(timeString)"
    }

    private var timeString: String {
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm"
        return fmt.string(from: now)
    }

    private var viewToggle: some View {
        Picker("View", selection: $viewMode) {
            ForEach(SchedulesViewMode.allCases) { mode in
                Text(mode.displayName)
                    .tag(mode)
            }
        }
        .pickerStyle(.segmented)
        .frame(width: 280)
        .disabled(false)
        // Note: SwiftUI's Picker can't disable individual segments. Workaround:
        // intercept timeline selection in the binding via SchedulesView, OR
        // accept that selecting Timeline shows an empty/placeholder pane.
        // For Plan 7 we let the segment select but render an "Available in
        // a future plan" placeholder in the master pane (handled by SchedulesView).
    }

    private var addNewButton: some View {
        Button(action: onAddSlot) {
            HStack(spacing: 4) {
                Image(systemName: "plus")
                Text("New")
            }
            .font(DS.sans(13, weight: .medium))
            .padding(.horizontal, 14)
            .padding(.vertical, 7)
            .background(DS.Accent.fill, in: RoundedRectangle(cornerRadius: 7))
            .foregroundStyle(DS.Paper.base)
        }
        .buttonStyle(.plain)
    }
}
```

- [ ] **Step 2: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/SchedulesHeader.swift
git commit -m "feat(app): SchedulesHeader — serif title + live subtitle + view toggle + + New button"
```

---

## Task 12: `SchedulesFilterChips`

**Files:**
- Create: `Scout/Schedules/SchedulesFilterChips.swift`

- [ ] **Step 1: Create `Scout/Schedules/SchedulesFilterChips.swift`**

```swift
import SwiftUI

/// Filter chips row above the master list. `All` + per-type chips with
/// derived counts. Single-select; clicking a type chip swaps selection.
/// Types with zero slots in the source list have their chip hidden.
struct SchedulesFilterChips: View {
    @Binding var filterMode: SchedulesFilterMode
    let slots: [Slot]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                allChip
                ForEach(SlotType.allCases, id: \.self) { type in
                    if SchedulesFilterMode.count(of: type, in: slots) > 0 {
                        typeChip(for: type)
                    }
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 8)
        }
    }

    private var allChip: some View {
        chip(
            label: "All",
            count: slots.count,
            isSelected: filterMode == .all,
            dotColor: nil
        ) {
            filterMode = .all
        }
    }

    private func typeChip(for type: SlotType) -> some View {
        chip(
            label: type.rawValue.capitalized,
            count: SchedulesFilterMode.count(of: type, in: slots),
            isSelected: filterMode == .type(type),
            dotColor: DS.SlotType.color(for: type)
        ) {
            filterMode = .type(type)
        }
    }

    @ViewBuilder
    private func chip(
        label: String,
        count: Int,
        isSelected: Bool,
        dotColor: Color?,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let dotColor {
                    Circle()
                        .fill(dotColor)
                        .frame(width: 6, height: 6)
                }
                Text(label)
                    .font(DS.sans(12, weight: .medium))
                Text("\(count)")
                    .font(DS.mono(11))
                    .foregroundStyle(isSelected ? DS.Paper.base.opacity(0.85) : DS.Ink.p3)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(
                Capsule().fill(isSelected ? DS.Ink.p1 : DS.Paper.raised)
            )
            .foregroundStyle(isSelected ? DS.Paper.base : DS.Ink.p2)
        }
        .buttonStyle(.plain)
    }
}
```

- [ ] **Step 2: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/SchedulesFilterChips.swift
git commit -m "feat(app): SchedulesFilterChips — All + per-type single-select chips"
```

---

## Task 13: `SchedulesDetailPane`

**Files:**
- Create: `Scout/Schedules/SchedulesDetailPane.swift`

- [ ] **Step 1: Create `Scout/Schedules/SchedulesDetailPane.swift`**

```swift
import SwiftUI

/// Detail pane in the master/detail layout. Wraps `SlotEditForm` for the
/// currently-selected slot; renders an empty-state when nothing is
/// selected. The parent (SchedulesView) decides what slot — including a
/// new draft — gets passed in.
struct SchedulesDetailPane: View {
    let slot: Slot?
    let isNewDraft: Bool
    let onSave: (Slot) async -> Void
    let onDelete: () async -> Void
    let onFireNow: (String) async -> Void
    let onRevertNewDraft: (() -> Void)?

    var body: some View {
        if let slot {
            content(for: slot)
        } else {
            emptyState
        }
    }

    @ViewBuilder
    private func content(for slot: Slot) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            header(for: slot)
            Divider().background(DS.Rule.soft)
            ScrollView {
                SlotEditForm(
                    liveSlot: slot,
                    isNewDraft: isNewDraft,
                    onSave: onSave,
                    onDelete: onDelete,
                    onFireNow: onFireNow,
                    onRevertNewDraft: onRevertNewDraft
                )
                .id(slot.key)
            }
        }
    }

    private func header(for slot: Slot) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(slot.key)
                .font(DS.mono(15, weight: .semibold))
                .foregroundStyle(DS.Ink.p1)
            TypePill(type: slot.type)
            Spacer()
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "cursorarrow.click")
                .font(.system(size: 36))
                .foregroundStyle(DS.Ink.p4)
            Text("Pick a slot to edit")
                .font(DS.serif(18, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
            Text("Click a row in the list to edit its time, weekdays, cooldown, and other settings.")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }
}
```

- [ ] **Step 2: Build**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/SchedulesDetailPane.swift
git commit -m "feat(app): SchedulesDetailPane — wraps SlotEditForm + empty-state for master/detail"
```

---

## Task 14: `SlotEditForm` restyle (DS tokens, button colors)

**Files:**
- Modify: `Scout/Schedules/SlotEditForm.swift`

- [ ] **Step 1: Update section labels to DS sans uppercase + Ink.p3**

Open `Scout/Schedules/SlotEditForm.swift`. Find each section's `Text("Time")`, `Text("Weekdays")`, `Text("On miss")`, `Text("Cooldown (minutes)")`, `Text("Runner")`, `Text("Missed window (hours)")`, `Text("Type")`, `Text("Runtime")`, `Text("Slot key")`. Replace each with the same string but font/color changed:

Replace the existing section-label pattern (something like `Text("Time").font(.callout).foregroundStyle(.secondary)`) with:

```swift
Text("Time")
    .font(DS.sans(11, weight: .medium))
    .tracking(1)
    .textCase(.uppercase)
    .foregroundStyle(DS.Ink.p3)
```

Apply this replacement to ALL section labels in the form. (Don't touch field-error labels — those stay `.font(.caption).foregroundStyle(.red)` for now; field errors use red consistently.)

- [ ] **Step 2: Restyle the action bar buttons**

Find the `actionBar` `@ViewBuilder`. Update each button's coloring:

- **Save button:** background `DS.Accent.fill`, foreground `DS.Paper.base`. Add a `.padding(.horizontal, 14).padding(.vertical, 6).background(DS.Accent.fill, in: RoundedRectangle(cornerRadius: 6)).foregroundStyle(DS.Paper.base)` modifier chain to the Save Button label.
- **Delete button:** keep red, but use `DS.Status.err` for the foreground.
- **Revert + Fire-now buttons:** foreground `DS.Ink.p2`.

Concrete: replace the existing action-bar Save button:

```swift
Button("Save") {
    if Self.requiresTypeChangeConfirmation(draft: draft, live: liveSlot) {
        isConfirmingTypeChange = true
    } else {
        Task { await performSave() }
    }
}
.keyboardShortcut(.return, modifiers: [.command])
.disabled(draft.firstError != nil || (!isNewDraft && !draft.isDirty(against: liveSlot)))
```

with:

```swift
Button {
    if Self.requiresTypeChangeConfirmation(draft: draft, live: liveSlot) {
        isConfirmingTypeChange = true
    } else {
        Task { await performSave() }
    }
} label: {
    Text("Save")
        .font(DS.sans(13, weight: .medium))
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .background(DS.Accent.fill, in: RoundedRectangle(cornerRadius: 6))
        .foregroundStyle(DS.Paper.base)
}
.buttonStyle(.plain)
.keyboardShortcut(.return, modifiers: [.command])
.disabled(draft.firstError != nil || (!isNewDraft && !draft.isDirty(against: liveSlot)))
```

Similarly restyle Delete + Revert + Fire-now to use plain button style + DS-token foregrounds. Keep all the `.alert(...)` and `.confirmationDialog(...)` modifiers below the action bar — those don't need restyling.

- [ ] **Step 3: Drop the form's outer container styling**

In Plan 6, `SlotEditForm.body` was wrapped in a `VStack { ... }.padding().background(Color(nsColor: .controlBackgroundColor).opacity(0.4)).cornerRadius(8)` block (it was a self-contained card). With the new `SchedulesDetailPane` providing the surface, we want to drop the outer card. Replace that container with a plain `VStack(alignment: .leading, spacing: 12) { ... }.padding(20)` (no background, no corner radius).

- [ ] **Step 4: Build + run existing form tests — expect 7/7 still passing**

```bash
xcodebuild test -only-testing:ScoutTests/SlotEditFormTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```

The form tests cover validators + isDirty + firstError + requiresTypeChangeConfirmation — none of those depend on visual styling, so they should be unaffected.

- [ ] **Step 5: Build whole app**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add Scout/Schedules/SlotEditForm.swift
git commit -m "feat(app): SlotEditForm restyle — DS section labels, Accent.fill Save, drop outer card"
```

---

## Task 15: `SchedulesView` rewrite (top-level wire-up)

**Files:**
- Modify: `Scout/Schedules/SchedulesView.swift` (full rewrite)
- Modify: `ScoutTests/Schedules/SchedulesViewTests.swift` (preserve existing helper tests; verify they still pass)

- [ ] **Step 1: Replace the entire body of `Scout/Schedules/SchedulesView.swift`**

```swift
import SwiftUI

struct SchedulesView: View {
    @EnvironmentObject var service: ScheduleEditService
    @EnvironmentObject var appState: AppState

    @SceneStorage("schedulesView") private var viewMode: SchedulesViewMode = .table
    @State private var filterMode: SchedulesFilterMode = .all
    @State private var selectedSlotKey: String?
    @State private var newDraftSlot: Slot?

    @State private var staleBannerVisible = false
    @State private var stalenessDetail: String?
    @State private var errorMessage: String?
    @State private var isInitialLoading = true

    var body: some View {
        NavigationSplitView {
            masterPane
                .navigationSplitViewColumnWidth(ideal: 720, max: .infinity)
        } detail: {
            SchedulesDetailPane(
                slot: detailSlot,
                isNewDraft: detailIsNewDraft,
                onSave: handleSave,
                onDelete: handleDelete,
                onFireNow: handleFireNow,
                onRevertNewDraft: detailIsNewDraft ? { newDraftSlot = nil; selectedSlotKey = nil } : nil
            )
            .navigationSplitViewColumnWidth(min: 360, ideal: 420)
        }
        .background(DS.Paper.base)
        .task { await reload() }
    }

    // MARK: - Master pane

    @ViewBuilder
    private var masterPane: some View {
        VStack(alignment: .leading, spacing: 0) {
            SchedulesHeader(
                slotCount: service.slots.count,
                typeCount: typeCount,
                viewMode: $viewMode,
                onAddSlot: addDraftSlot
            )
            Divider().background(DS.Rule.hard)

            if staleBannerVisible {
                staleBanner
            }
            if let err = errorMessage {
                errorBanner(err)
            }

            SchedulesFilterChips(filterMode: $filterMode, slots: service.slots)
            Divider().background(DS.Rule.soft)

            content
        }
    }

    @ViewBuilder
    private var content: some View {
        if isInitialLoading {
            VStack {
                Spacer()
                ProgressView()
                Spacer()
            }
            .frame(maxWidth: .infinity)
        } else if filteredSlots.isEmpty && newDraftSlot == nil {
            emptyState
        } else {
            switch viewMode {
            case .table:
                SchedulesMasterTable(
                    slots: filteredSlots,
                    newDraftSlot: newDraftSlot,
                    selectedSlotKey: $selectedSlotKey
                )
            case .cards:
                SchedulesMasterCards(
                    slots: filteredSlots,
                    newDraftSlot: newDraftSlot,
                    selectedSlotKey: $selectedSlotKey
                )
            case .timeline:
                timelinePlaceholder
            }
        }
    }

    private var timelinePlaceholder: some View {
        VStack(spacing: 8) {
            Spacer()
            Image(systemName: "clock.arrow.circlepath")
                .font(.system(size: 32))
                .foregroundStyle(DS.Ink.p4)
            Text("Timeline view coming in a future plan")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "calendar.badge.plus")
                .font(.system(size: 36))
                .foregroundStyle(DS.Ink.p4)
            Text("No scheduled slots")
                .font(DS.serif(20, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
            Text("Add a slot to start scheduling Scout runs. Or run `scoutctl schedule init` from the terminal to seed the plugin defaults (10 standard slots).")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
            Button("+ Add slot") { addDraftSlot() }
                .buttonStyle(.borderedProminent)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Banners

    @ViewBuilder
    private var staleBanner: some View {
        HStack(alignment: .firstTextBaseline) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(DS.Status.warn)
            VStack(alignment: .leading, spacing: 2) {
                Text("schedule.yaml was modified externally")
                    .font(DS.sans(13, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                if let detail = stalenessDetail {
                    Text(detail).font(DS.sans(11)).foregroundStyle(DS.Ink.p3)
                }
            }
            Spacer()
            Button("Reload now") {
                Task { await reload(); staleBannerVisible = false }
            }
            .buttonStyle(.borderedProminent)
            Button("Dismiss") { staleBannerVisible = false }
                .buttonStyle(.plain)
                .foregroundStyle(DS.Ink.p3)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 10)
        .background(DS.Status.warn.opacity(0.15))
    }

    @ViewBuilder
    private func errorBanner(_ text: String) -> some View {
        HStack {
            Image(systemName: "xmark.octagon.fill").foregroundStyle(DS.Status.err)
            Text(text).font(DS.sans(13)).foregroundStyle(DS.Ink.p1)
            Spacer()
            Button("Dismiss") { errorMessage = nil }
                .buttonStyle(.plain)
                .foregroundStyle(DS.Ink.p3)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 10)
        .background(DS.Status.err.opacity(0.12))
    }

    // MARK: - Derived values

    private var filteredSlots: [Slot] {
        filterMode.apply(to: service.slots)
    }

    private var typeCount: Int {
        Set(service.slots.map(\.type)).count
    }

    /// Slot to render in the detail pane: prefer the explicitly-selected
    /// slot, falling back to the new draft (which auto-selects on creation).
    private var detailSlot: Slot? {
        if let key = selectedSlotKey {
            if let draft = newDraftSlot, draft.key == key { return draft }
            return service.slots.first { $0.key == key }
        }
        return nil
    }

    private var detailIsNewDraft: Bool {
        if let key = selectedSlotKey, let draft = newDraftSlot, draft.key == key {
            return true
        }
        return false
    }

    // MARK: - Static helpers (preserved from Plan 6 — tested in SchedulesViewTests)

    static func nextNewSlotKey(existing: [String]) -> String {
        var n = 1
        while existing.contains("new-slot-\(n)") { n += 1 }
        return "new-slot-\(n)"
    }

    static func makeNewDraftSlot(key: String) -> Slot {
        Slot(
            key: key,
            type: .briefing,
            runner: "run-scout.sh",
            firesAtLocal: "09:00",
            weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
            missedWindowHours: 4,
            onMiss: .fire,
            cooldownMinutes: 60,
            budgetUsd: nil,
            tz: nil,
            runtime: .local
        )
    }

    // MARK: - Actions

    private func addDraftSlot() {
        guard newDraftSlot == nil else {
            // If a draft already exists, just re-select it.
            selectedSlotKey = newDraftSlot?.key
            return
        }
        let existing = service.slots.map(\.key)
        let key = Self.nextNewSlotKey(existing: existing)
        let draft = Self.makeNewDraftSlot(key: key)
        newDraftSlot = draft
        selectedSlotKey = key
    }

    private func reload() async {
        do {
            try await service.loadAll()
            isInitialLoading = false
        } catch {
            errorMessage = error.localizedDescription
            isInitialLoading = false
        }
    }

    private func handleSave(_ saved: Slot) async {
        if detailIsNewDraft {
            await saveNewDraft(saved)
        } else {
            // Find the original slot by selected key.
            guard let key = selectedSlotKey,
                  let original = service.slots.first(where: { $0.key == key }) else { return }
            await saveExistingSlot(saved, original: original)
        }
    }

    private func saveNewDraft(_ slot: Slot) async {
        var combined = service.slots
        combined.append(slot)
        do {
            try await service.save(allSlots: combined)
            newDraftSlot = nil
            selectedSlotKey = slot.key
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func saveExistingSlot(_ saved: Slot, original: Slot) async {
        var updated = service.slots
        if let idx = updated.firstIndex(where: { $0.key == original.key }) {
            updated[idx] = saved
        }
        do {
            try await service.save(allSlots: updated)
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func handleDelete() async {
        guard let key = selectedSlotKey,
              let slot = service.slots.first(where: { $0.key == key }) else { return }
        do {
            try await service.delete(slotKey: slot.key)
            if selectedSlotKey == slot.key { selectedSlotKey = nil }
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func handleFireNow(_ key: String) async {
        await appState.fireNow(slotKey: key, bypassBudget: false)
    }
}
```

- [ ] **Step 2: Build — expect compile errors pointing at Plan 6's SlotRow + SlotSummaryRow references**

```bash
cd /Users/jordanburger/scout-app
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -20
```

The `SlotRow.swift` + `SlotSummaryRow.swift` files still exist but are no longer referenced. They should compile cleanly in isolation but Plan 6's `SchedulesView` is gone, so they're dead code. They get deleted in Task 16.

- [ ] **Step 3: Run all the schedule-related test suites**

```bash
xcodebuild test \
    -only-testing:ScoutTests/SchedulesViewTests \
    -only-testing:ScoutTests/SlotEditFormTests \
    -only-testing:ScoutTests/ScheduleEditServiceTests \
    -only-testing:ScoutTests/SlotTests \
    -project Scout.xcodeproj -scheme Scout 2>&1 | tail -15
```

Expected: all pass. The Plan 6 `SchedulesViewTests` only test the static helpers (`nextNewSlotKey` + `makeNewDraftSlot`) which are preserved verbatim above.

- [ ] **Step 4: Commit**

```bash
git add Scout/Schedules/SchedulesView.swift
git commit -m "feat(app): SchedulesView rewrite — NavigationSplitView, header, filter chips, view toggle"
```

---

## Task 16: Delete `SlotRow` + `SlotSummaryRow` and their tests

**Files:**
- Delete: `Scout/Schedules/SlotRow.swift`
- Delete: `Scout/Schedules/SlotSummaryRow.swift`
- Delete: `ScoutTests/Schedules/SlotSummaryRowTests.swift`

- [ ] **Step 1: `git rm` the obsolete files**

```bash
cd /Users/jordanburger/scout-app
git rm Scout/Schedules/SlotRow.swift
git rm Scout/Schedules/SlotSummaryRow.swift
git rm ScoutTests/Schedules/SlotSummaryRowTests.swift
```

- [ ] **Step 2: Build — expect BUILD SUCCEEDED**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -5
```
If anything fails, it means a stray reference exists somewhere — grep + fix.

```bash
grep -rn "SlotRow\b\|SlotSummaryRow\b" Scout/ ScoutTests/ --include="*.swift" 2>&1 | head -10
```

Expected: empty (no stray references).

- [ ] **Step 3: Run full test suite**

```bash
xcodebuild test -project Scout.xcodeproj -scheme Scout 2>&1 | tail -25
```

Expected: all tests pass except the pre-existing `ConnectorHealthServiceTests/buildsMatrixFromFixtureAndFiltersAckedAlerts` flake (Plan 5/6 inheritance, unrelated to this plan).

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(app): remove SlotRow + SlotSummaryRow — replaced by master/detail pattern"
```

---

## Task 17: Smoke + push + open PR + merge

- [ ] **Step 1: Smoke-launch the app**

```bash
osascript -e 'tell application "Scout" to quit' 2>&1 ; sleep 2
cd /Users/jordanburger/scout-app
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
open ~/Library/Developer/Xcode/DerivedData/Scout-*/Build/Products/Debug/Scout.app
sleep 2
pgrep -fl "/Scout.app/Contents/MacOS/Scout"
```

Manually verify:
- Click Schedules → 10 slots populate in Table view (default).
- Switch to Cards → grid layout renders.
- Click a row/card → detail pane shows form with `SlotEditForm`.
- Click + New → draft row at top, auto-selected, editor in detail pane.
- Click All / Briefing / Consolidation chips → list filters correctly.
- Type counts match the slot data.
- Live time updates within ~60s in the subtitle.

- [ ] **Step 2: Push branch**

```bash
git push -u origin plan-7-schedules-visual
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --title "Plan 7: Schedules tab visual rewrite" --body "$(cat <<'EOF'
## Summary

UI-only rewrite of the Schedules tab to match scout-app's editorial DS language. Replaces Plan 6's inline-expand pattern with master/detail (NavigationSplitView), adds Table + Cards view toggle, type-color palette, filter chips, and a live-time-refreshing header.

~17 commits, build SUCCEEDED, ~16 new tests across 4 new test suites, all Plan 6 service-layer + form tests still green.

### What's new

- `DS.SlotType` color palette (briefing/consolidation/dreaming/research/manual). Distinct from `DS.Accent.fill` and `DS.Priority.*`.
- Master/detail layout via `NavigationSplitView`. Click a row/card → detail pane shows `SlotEditForm`.
- View toggle: Table | Cards | Timeline (Timeline disabled with "future plan" placeholder).
- Header: serif title, live subtitle (`N active · M types · now HH:MM`), view toggle, orange `+ New` button.
- Filter chips: All + per-type single-select with derived counts; types with 0 slots hide their chip.
- Reusable presentational components: `TypePill`, `OnMissPill`, `DayCircleStrip`, `WeekdaysFormatter`.

### What's preserved

All Plan 5/6 service-layer behavior — atomic save with mtime stale-check + tmpfile cleanup + header-comment preservation, type-change confirm, delete confirm, draft-row-in-master flow, fire-now via `AppState.fireNow`, error banners, stale-edit banner.

### What's deleted

- `Scout/Schedules/SlotRow.swift` — Plan 6's inline-expand container is gone
- `Scout/Schedules/SlotSummaryRow.swift` — replaced by `SlotTableRow` + `SlotCard`
- `ScoutTests/Schedules/SlotSummaryRowTests.swift` — view deleted

## Test plan

- [x] xcodebuild build clean
- [x] All new test suites green: `DSSlotTypeTests`, `WeekdaysFormatterTests`, `SchedulesViewModeTests`, `SchedulesFilterModeTests`
- [x] Plan 5/6 service + form tests still pass: `SlotTests`, `ScheduleEditServiceTests`, `SlotEditFormTests`, `SchedulesViewTests`
- [x] Manual smoke: 10 slots populate; Table↔Cards toggle works; row select → detail edit; + New flow; filter chips
- [ ] Manual smoke after merge: edit slot via UI, watch dispatcher honor at next 5-min tick

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Merge after CI/review**

scout-app has no CI (Mac-only). Merge directly:

```bash
gh pr merge --merge --delete-branch
```

- [ ] **Step 5: Sync local main**

```bash
cd /Users/jordanburger/scout-app
git stash push -m "carryforwards" -- docs/superpowers/FOLLOWUPS.md .gitignore 2>&1 | tail -2 || true
git checkout main
git pull --ff-only
git branch -d plan-7-schedules-visual
git stash pop 2>&1 | tail -2 || true
```

- [ ] **Step 6: Final smoke after merge**

```bash
osascript -e 'tell application "Scout" to quit' 2>&1 ; sleep 2
cd /Users/jordanburger/scout-app
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
open ~/Library/Developer/Xcode/DerivedData/Scout-*/Build/Products/Debug/Scout.app
```

**Plan 7 complete.**
