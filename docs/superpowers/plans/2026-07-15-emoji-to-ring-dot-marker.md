# Ring+Dot Marker (replacing emoji color-dots) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the emoji "color dots" and category pictographs in the Scout UI with a single palette-native ring+dot marker, and sweep the remaining decorative emoji / glyph-as-icon usages to SF Symbols.

**Architecture:** A new reusable `KindMarker` SwiftUI view (soft tinted ring + solid dot for priority kinds, or ring + small SF Symbol for category kinds) plus a `DS.kindSymbol(_:)` mapper. Three surfaces (section headers, board columns, filter chips) adopt it; `DS.kindGlyph` is deleted. A follow-up sweep converts standalone status glyphs (`✓ ✗ ⚠ ● ! ·`, fullwidth `＋`) to SF Symbols / ASCII. Parser/data emoji are untouched.

**Tech Stack:** Swift, SwiftUI, Swift Testing (`@Test`/`@Suite`), xcodebuild.

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-07-15-emoji-to-ring-dot-marker-design.md`.
- Branch `adamvyborny-emoji-ring-dot-markers` is already checked out with the spec committed. Do all work here.
- Build/test: `DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<Suite>`. `xcode-select` points at CommandLineTools, so the `DEVELOPER_DIR` prefix is required for every xcodebuild call.
- SourceKit / IDE may show "Cannot find type … in scope" or "No such module 'Testing'" — these are false positives; `xcodebuild` is authoritative.
- **Do NOT touch parser/data emoji** (behaviour-load-bearing, matched against vault markdown `scoutctl` writes): `Scout/ActionItems/ActionItemsParser.swift:252` (snooze regex), `:525` (`recognizedEmojiPrefixes`), `:554–560` (emoji→Kind map); `Scout/ActionItems/Models/ActionTask.swift:128` (priority strip), `:133` (status strip).
- **Do NOT touch** `parser-corpus.json` or any fixture. This work does not alter parsing, so the three-repo corpus sync / checksum steps in `CLAUDE.md` do not apply.
- **Keep** legitimate typography: `·` separators, `…` ellipses, `⌘⇧↵` keyboard hints, `→` in prose, list bullets `•`, and the `✓`/`✗` inside `ActivityHeatmapView.swift:248/249` accessibility/tooltip strings.
- Conventional-commit messages. No `Co-Authored-By` line, no "Generated with Claude" attribution.

---

### Task 1: `KindMarker` view + `DS.kindSymbol(_:)` mapper

**Files:**
- Create: `Scout/Utilities/KindMarker.swift`
- Modify: `Scout/Utilities/DesignSystem.swift` (add `kindSymbol(_:)` inside the `enum DS` extension that already holds `kindGlyph`/`priorityColor`, near line 101)
- Test: `ScoutTests/Utilities/DSKindSymbolTests.swift` (create; mirrors `ScoutTests/Schedules/DSSlotTypeTests.swift`)

**Interfaces:**
- Produces: `DS.kindSymbol(_ kind: ActionSection.Kind) -> String?` — returns an SF Symbol name for category kinds, `nil` for the priority axis + neutral (they render as a plain dot).
- Produces: `struct KindMarker: View { let kind: ActionSection.Kind; var size: CGFloat = 14 }` — the reusable marker consumed by Tasks 2–4.

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/Utilities/DSKindSymbolTests.swift`:

```swift
import Testing
import SwiftUI
@testable import Scout

@Suite("DS.kindSymbol")
struct DSKindSymbolTests {

    @Test("Priority + neutral kinds have no symbol (they render as a dot)")
    @MainActor
    func test_priority_kinds_have_no_symbol() {
        #expect(DS.kindSymbol(.urgent)   == nil)
        #expect(DS.kindSymbol(.todo)     == nil)
        #expect(DS.kindSymbol(.watching) == nil)
        #expect(DS.kindSymbol(.neutral)  == nil)
    }

    @Test("Category kinds map to their SF Symbol name")
    @MainActor
    func test_category_kinds_map_to_symbols() {
        #expect(DS.kindSymbol(.done)     == "checkmark")
        #expect(DS.kindSymbol(.personal) == "house")
        #expect(DS.kindSymbol(.focus)    == "lightbulb")
        #expect(DS.kindSymbol(.meetings) == "calendar")
        #expect(DS.kindSymbol(.digest)   == "list.clipboard")
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/DSKindSymbolTests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: FAIL — compile error, `kindSymbol` not a member of `DS`.

- [ ] **Step 3: Add `kindSymbol(_:)` to `DesignSystem.swift`**

Immediately after the `kindGlyph(_:)` function (ends at `DesignSystem.swift:115`, before the closing `}` of the `extension`/`enum`), add:

```swift
    /// SF Symbol shown inside a `KindMarker` for category kinds. Returns nil
    /// for kinds that render as a plain colored dot (the priority axis + neutral).
    static func kindSymbol(_ kind: ActionSection.Kind) -> String? {
        switch kind {
        case .urgent, .todo, .watching, .neutral: return nil
        case .done:     return "checkmark"
        case .personal: return "house"
        case .focus:    return "lightbulb"
        case .meetings: return "calendar"
        case .digest:   return "list.clipboard"
        }
    }
```

- [ ] **Step 4: Create the `KindMarker` view**

Create `Scout/Utilities/KindMarker.swift`:

```swift
import SwiftUI

/// Concentric status marker: a soft tinted ring wrapping either a solid
/// colored dot (priority kinds) or a small SF Symbol (category kinds).
/// Replaces the emoji "color dots" that used to render in section headers,
/// board columns, and filter chips.
struct KindMarker: View {
    let kind: ActionSection.Kind
    var size: CGFloat = 14

    var body: some View {
        let hue = DS.priorityColor(kind)
        ZStack {
            Circle()
                .strokeBorder(hue.opacity(0.4), lineWidth: 1.5)
            if let symbol = DS.kindSymbol(kind) {
                Image(systemName: symbol)
                    .font(.system(size: size * 0.5, weight: .medium))
                    .foregroundStyle(hue)
            } else {
                Circle()
                    .fill(hue)
                    .frame(width: size * 0.42, height: size * 0.42)
            }
        }
        .frame(width: size, height: size)
    }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/DSKindSymbolTests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: `TEST SUCCEEDED`, both tests passing.

- [ ] **Step 6: Commit**

```bash
git add Scout/Utilities/KindMarker.swift Scout/Utilities/DesignSystem.swift ScoutTests/Utilities/DSKindSymbolTests.swift
git commit -m "feat(design): add KindMarker view and DS.kindSymbol mapper"
```

---

### Task 2: Adopt `KindMarker` in section headers; delete `kindGlyph`

**Files:**
- Modify: `Scout/ActionItems/Views/SectionView.swift:48` (header glyph), `:74–76` (remove `glyph`), `:148` (completed `✓`)
- Modify: `Scout/Utilities/DesignSystem.swift:102–115` (delete `kindGlyph`)
- Modify: `Scout/ActionItems/ActionItemsView.swift:421` (synthesized `emoji: "✅"`)

**Interfaces:**
- Consumes: `KindMarker(kind:size:)` from Task 1.

- [ ] **Step 1: Replace the header glyph with `KindMarker`**

In `SectionView.swift`, in the `header` view, replace:

```swift
            Text(glyph)
                .font(DS.sans(13))
                .frame(width: 18, alignment: .leading)
```

with:

```swift
            KindMarker(kind: section.kind, size: 14)
                .frame(width: 18, alignment: .leading)
                .alignmentGuide(.firstTextBaseline) { d in d[VerticalAlignment.center] + 4 }
```

(The `alignmentGuide` keeps the marker centred on the uppercase label's baseline; the header `HStack` uses `.firstTextBaseline`. The `+ 4` offset is a starting value — tune it in Step 5's visual check.)

- [ ] **Step 2: Remove the now-unused `glyph` property**

Delete these lines from `SectionView.swift` (were `:74–76`):

```swift
    private var glyph: String {
        section.emoji.isEmpty ? DS.kindGlyph(section.kind) : section.emoji
    }
```

- [ ] **Step 3: Convert the completed-item `✓` marker to an SF Symbol**

In `SectionView.swift`, in `completedList`, replace:

```swift
                        Text("✓")
                            .font(DS.mono(11))
                            .foregroundStyle(DS.Priority.done)
```

with:

```swift
                        Image(systemName: "checkmark")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(DS.Priority.done)
```

- [ ] **Step 4: Delete `kindGlyph` and clean the synthesized emoji**

In `DesignSystem.swift`, delete the entire `kindGlyph(_:)` function (the doc comment + `static func kindGlyph … }`, was `:102–115`). It has no other callers.

In `ActionItemsView.swift:421`, change:

```swift
                emoji: "✅",
```

to:

```swift
                emoji: "",
```

(The header no longer renders `section.emoji`; this removes the last literal emoji feeding a synthesized section. The `emoji` field stays on the model — it is now vestigial for display but out of scope to remove.)

- [ ] **Step 5: Build and visually verify**

Run: `DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | grep -iE "error:|BUILD (SUCCEEDED|FAILED)"`
Expected: `BUILD SUCCEEDED` (no "kindGlyph" unresolved-reference errors).

Then launch the app and open Action Items. Confirm: every section header shows a ring+dot marker (urgent/to-do/watching = colored dot; focus=lightbulb, meetings=calendar, digest=clipboard, done=checkmark), the marker sits on the label baseline (adjust the `+ 4` offset from Step 1 if it looks high/low), and no emoji remain in headers. Check both light and dark appearance.

If the digest marker renders blank, `list.clipboard` is unavailable on the SF Symbols set for the deployment target — fall back to `doc.plaintext` in `DS.kindSymbol(.digest)` (`DesignSystem.swift`) and rebuild.

- [ ] **Step 6: Commit**

```bash
git add Scout/ActionItems/Views/SectionView.swift Scout/Utilities/DesignSystem.swift Scout/ActionItems/ActionItemsView.swift
git commit -m "feat(action-items): render section headers with KindMarker, drop kindGlyph emoji"
```

---

### Task 3: Adopt `KindMarker` in board column headers

**Files:**
- Modify: `Scout/ActionItems/Views/BoardView.swift:56–58`

**Interfaces:**
- Consumes: `KindMarker(kind:size:)` from Task 1.

- [ ] **Step 1: Replace the flat circle with `KindMarker`**

In `BoardView.swift`, in the `header(_:collapsed:)` builder, replace:

```swift
            Circle()
                .fill(DS.priorityColor(column.kind))
                .frame(width: 8, height: 8)
```

with:

```swift
            KindMarker(kind: column.kind, size: 14)
```

- [ ] **Step 2: Build and visually verify**

Run: `DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | grep -iE "error:|BUILD (SUCCEEDED|FAILED)"`
Expected: `BUILD SUCCEEDED`.

Launch the app, switch Action Items to the board view. Confirm each column header shows the ring+dot marker in place of the old flat dot, aligned with the title (`HStack(spacing: 8)`, centre-aligned). Check light + dark.

- [ ] **Step 3: Commit**

```bash
git add Scout/ActionItems/Views/BoardView.swift
git commit -m "feat(action-items): render board column headers with KindMarker"
```

---

### Task 4: Adopt `KindMarker` in filter chips

**Files:**
- Modify: `Scout/ActionItems/Views/FilterChipsView.swift` — `chipButton` signature + dot render block (`:85`, `:97`, `:110–130`)

**Interfaces:**
- Consumes: `KindMarker(kind:size:)` from Task 1.
- Changes `chipButton`'s `dot: Color?` parameter to `kind: ActionSection.Kind?`.

- [ ] **Step 1: Change the `chipButton` parameter**

In `FilterChipsView.swift`, change the signature:

```swift
    private func chipButton(
        label: String,
        dot: Color?,
        count: Int?,
        selected: Bool,
        action: @escaping () -> Void
    ) -> some View {
```

to:

```swift
    private func chipButton(
        label: String,
        kind: ActionSection.Kind?,
        count: Int?,
        selected: Bool,
        action: @escaping () -> Void
    ) -> some View {
```

- [ ] **Step 2: Replace the dot render block**

In the same function, replace:

```swift
                if let dot {
                    Circle()
                        .fill(dot)
                        .frame(width: 8, height: 8)
                        .overlay(
                            Circle().strokeBorder(DS.Paper.base.opacity(0.8), lineWidth: 2)
                                .frame(width: 12, height: 12)
                        )
                        .frame(width: 8, height: 8)
                }
```

with:

```swift
                if let kind {
                    KindMarker(kind: kind, size: 12)
                }
```

- [ ] **Step 3: Update the two callers**

In `allChip`, change `dot: nil,` to `kind: nil,`.
In `kindChip(_:label:)`, change `dot: DS.priorityColor(kind),` to `kind: kind,`.

- [ ] **Step 4: Build and visually verify**

Run: `DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | grep -iE "error:|BUILD (SUCCEEDED|FAILED)"`
Expected: `BUILD SUCCEEDED`.

Launch the app, open the Action Items filter chip row. Confirm each kind chip shows a 12px ring+dot marker, the "All" chip has none, selection still highlights, and category chips (focus/meetings/etc.) show their symbol. Check light + dark.

- [ ] **Step 5: Commit**

```bash
git add Scout/ActionItems/Views/FilterChipsView.swift
git commit -m "feat(action-items): render filter chips with KindMarker"
```

---

### Task 5: Sweep connector-health + alert-banner status glyphs

**Files:**
- Modify: `Scout/ControlCenter/ConnectorHealthRailCard.swift:52` (`⚠`), `:90` (`✓%`), `:127–132` (cell `✓ ✗ ! ·`)
- Modify: `Scout/ControlCenter/ConnectorAlertBanner.swift:28` (`⚠`)

- [ ] **Step 1: Convert the connector-health cell glyphs**

In `ConnectorHealthRailCard.swift`, in `cellView(_:)`, replace:

```swift
        case .ok:      Text("✓").foregroundStyle(DS.Status.ok)
        case .error:   Text("✗").foregroundStyle(DS.Status.err)
        case .partial: Text("!").foregroundStyle(DS.Status.warn)
        case .absent:  Text("·").foregroundStyle(DS.Ink.p4)
```

with:

```swift
        case .ok:      Image(systemName: "checkmark").imageScale(.small).foregroundStyle(DS.Status.ok)
        case .error:   Image(systemName: "xmark").imageScale(.small).foregroundStyle(DS.Status.err)
        case .partial: Image(systemName: "exclamationmark").imageScale(.small).foregroundStyle(DS.Status.warn)
        case .absent:  Image(systemName: "minus").imageScale(.small).foregroundStyle(DS.Ink.p4)
```

- [ ] **Step 2: Convert the roster-fallback `⚠`**

In `rosterFallbackBanner(reason:)`, replace:

```swift
            Text("⚠")
                .font(DS.mono(12))
                .foregroundStyle(DS.Status.warn)
```

with:

```swift
            Image(systemName: "exclamationmark.triangle")
                .font(DS.mono(12))
                .foregroundStyle(DS.Status.warn)
```

- [ ] **Step 3: Convert the `✓%` column header**

Replace:

```swift
                Text("✓%")
                    .font(DS.mono(10))
                    .foregroundStyle(DS.Ink.p4)
                    .frame(width: 40, alignment: .trailing)
                    .help("Health rate across the visible sessions where this connector was actually called.")
```

with:

```swift
                HStack(spacing: 1) {
                    Image(systemName: "checkmark").imageScale(.small)
                    Text("%")
                }
                .font(DS.mono(10))
                .foregroundStyle(DS.Ink.p4)
                .frame(width: 40, alignment: .trailing)
                .help("Health rate across the visible sessions where this connector was actually called.")
```

- [ ] **Step 4: Convert the alert-banner `⚠`**

In `ConnectorAlertBanner.swift`, in `bannerView(alerts:)`, replace:

```swift
                Text("⚠").font(DS.mono(13))
```

with:

```swift
                Image(systemName: "exclamationmark.triangle").font(DS.mono(13))
```

(No explicit colour — it inherits the banner's white foreground, matching the old glyph.)

- [ ] **Step 5: Build and visually verify**

Run: `DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | grep -iE "error:|BUILD (SUCCEEDED|FAILED)"`
Expected: `BUILD SUCCEEDED`.

Launch the app, open Control Center. Confirm the connector-health grid cells show checkmark/xmark/exclamation/minus symbols aligned in their 22pt columns, the `✓ %` header reads cleanly, and any warning banner shows a triangle icon. If the cell symbols look too large/small versus the mono row labels, adjust `.imageScale(.small)` to an explicit `.font(DS.mono(11))`. Check light + dark.

- [ ] **Step 6: Commit**

```bash
git add Scout/ControlCenter/ConnectorHealthRailCard.swift Scout/ControlCenter/ConnectorAlertBanner.swift
git commit -m "feat(control-center): swap connector-health status glyphs for SF Symbols"
```

---

### Task 6: Sweep the now-strip status tick and per-file empty-state `＋`

**Files:**
- Modify: `Scout/ControlCenter/NowStripView.swift:63` (resolved-run sub), `:125–127` (`tick(for:)` → `statusIcon(for:)`)
- Modify: `Scout/PerFileItems/Views/PerFileListView.swift:93`, `:113` (fullwidth `＋`)

- [ ] **Step 1: Replace `tick(for:)` with an SF Symbol mapper**

In `NowStripView.swift`, replace:

```swift
    private func tick(for status: RunStatus) -> String {
        status == .success ? "✓" : status == .running ? "●" : "✗"
    }
```

with:

```swift
    private func statusIcon(for status: RunStatus) -> String {
        status == .success ? "checkmark" : status == .running ? "circle.fill" : "xmark"
    }
```

- [ ] **Step 2: Restructure the resolved-run row to lead with the symbol**

In the `live`/`resolved` builder, replace the resolved branch:

```swift
            } else if let r = resolved {
                bigName(r.displayName)
                sub(
                    "\(tick(for: r.status)) \(r.status.rawValue) · \(r.startedAt.formatted(.relative(presentation: .named))) · \(r.commits.count) commit\(r.commits.count == 1 ? "" : "s")",
                    color: r.status == .success ? DS.Status.ok : DS.Status.err
                )
            } else if let r = runs.first {
```

with:

```swift
            } else if let r = resolved {
                bigName(r.displayName)
                HStack(spacing: 4) {
                    Image(systemName: statusIcon(for: r.status)).imageScale(.small)
                    Text("\(r.status.rawValue) · \(r.startedAt.formatted(.relative(presentation: .named))) · \(r.commits.count) commit\(r.commits.count == 1 ? "" : "s")")
                }
                .font(DS.mono(12))
                .foregroundStyle(r.status == .success ? DS.Status.ok : DS.Status.err)
            } else if let r = runs.first {
```

(The other `sub(...)` calls — running/orphaned/empty — are unchanged.)

- [ ] **Step 3: Replace the fullwidth `＋` in per-file empty states**

In `PerFileListView.swift`, in both empty-state messages, change the fullwidth `＋` to an ASCII `+`:

Line ~93: `message: "No \(config.title.lowercased()) folder found yet. Use + to add the first item."`
Line ~113: `message: "Nothing here yet. Use + to add a \(config.addNoun)."`

- [ ] **Step 4: Build and visually verify**

Run: `DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild build -scheme Scout -destination 'platform=macOS' 2>&1 | grep -iE "error:|BUILD (SUCCEEDED|FAILED)"`
Expected: `BUILD SUCCEEDED` (no unused-`tick` warnings; `tick` is fully removed).

Launch the app: the Control Center now-strip resolved-run line should lead with a checkmark/xmark icon; the per-file (wishlist/research) empty states should read "Use + to add …". Check light + dark.

- [ ] **Step 5: Commit**

```bash
git add Scout/ControlCenter/NowStripView.swift Scout/PerFileItems/Views/PerFileListView.swift
git commit -m "feat(control-center): swap now-strip tick and per-file plus glyph for symbols"
```

---

### Task 7: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: `TEST SUCCEEDED`. This includes `ParserContractTests`, proving the parser/data emoji were left intact.

- [ ] **Step 2: Confirm no stray decorative emoji remain in scope**

Run: `grep -rnP "[\x{1F000}-\x{1FAFF}\x{2600}-\x{27BF}\x{2B00}-\x{2BFF}]" Scout --include="*.swift" | grep -vE "ActionItemsParser\.swift|Models/ActionTask\.swift" | grep -vE "^\s*[^:]+:[0-9]+:\s*//"`
Expected: only the ActivityHeatmap tooltip `✓`/`✗` (intentional content) and any remaining keyboard/arrow/separator typography that is explicitly kept per the Global Constraints. No `🔴🟡🟢🏡💡📅📋✅` and no standalone `⚠`/`●` icons in view code.

- [ ] **Step 3: Final light/dark walkthrough**

Launch the app and confirm end-to-end: Action Items (list + board + filter chips), Control Center (connector health, now-strip, alert banner), and the per-file tabs' empty states. No emoji chrome anywhere; markers and symbols read cleanly in both appearances.

- [ ] **Step 4: (no commit — verification only)**

If Step 2 surfaces an unexpected decorative emoji, add a follow-up edit + commit in the style of Tasks 5–6 before considering the plan done.
