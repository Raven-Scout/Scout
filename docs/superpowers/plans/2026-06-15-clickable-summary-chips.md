# Clickable Summary Chips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the grey summary chip row under each collapsed task card in the Action Items **List** view clickable — single-item chips open their target, multi-item chips show a dropdown, and the repo chip opens the GitHub repo homepage.

**Architecture:** `TaskChip.chips(for:)` (the single, unit-tested derivation point) gains a `links: [Link]` field per chip carrying the click targets. `chipRow` in `TaskCardView` then renders each chip as static text (0 links), a `Button` (1 link), or a `Menu` dropdown (>1 links), reusing the established `.plainHit` button style, `EditorialChipBackground`, and `NSCursor.pointingHand` hover patterns.

**Tech Stack:** Swift, SwiftUI, AppKit (`NSWorkspace`/`NSCursor`), Swift Testing (`@Test`/`#expect`), Xcode.

---

## File Structure

- **Modify** `Scout/ActionItems/Views/TaskChip.swift` — add `TaskChip.Link`, a `links` stored property (defaulted in a custom `init`), and populate `links` for each chip in `chips(for:)`.
- **Modify** `ScoutTests/ActionItems/TaskChipTests.swift` — add tests asserting chips carry the right URLs; existing tests stay green.
- **Modify** `Scout/ActionItems/Views/TaskCardView.swift` — replace the inline chip rendering in `chipRow` with a `chipView(for:)` that branches on `links.count`; extract the shared `chipBody(for:)`; add `import AppKit`.

No other files change. `TaskDeepLink`, `ActionItemsParser`, `TaskLinksView`, and the Board view (`BoardCardView`) are untouched.

---

## Local commands

Run unit tests (only the chip suite):

```bash
DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild test \
  -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' \
  -only-testing:ScoutTests/TaskChipTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""
```

Build the app (Task 2 verification):

```bash
DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild build \
  -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' \
  CODE_SIGNING_ALLOWED=NO
```

---

## Task 1: `TaskChip` carries click targets

**Files:**
- Modify: `Scout/ActionItems/Views/TaskChip.swift`
- Test: `ScoutTests/ActionItems/TaskChipTests.swift`

- [ ] **Step 1: Write the failing tests**

Append these four tests inside the `TaskChipTests` suite in `ScoutTests/ActionItems/TaskChipTests.swift`, before the closing brace:

```swift
    @Test func prChipCarriesPRUrls() {
        let chips = TaskChip.chips(for: task(links: [pr("keboola/crm", 925)]))
        let prChip = chips.first { $0.label == "1 PR" }
        #expect(prChip?.links.map(\.url) == [URL(string: "https://github.com/keboola/crm/pull/925")!])
    }

    @Test func repoChipOpensRepoHomepage() {
        let chips = TaskChip.chips(for: task(links: [pr("keboola/crm", 925)]))
        let repoChip = chips.first { $0.label == "keboola/crm" }
        #expect(repoChip?.links.map(\.url) == [URL(string: "https://github.com/keboola/crm")!])
    }

    @Test func multiPRChipListsEachPR() {
        let chips = TaskChip.chips(for: task(links: [pr("a/b", 1), pr("a/b", 2)]))
        let prChip = chips.first { $0.label == "2 PRs" }
        #expect(prChip?.links.count == 2)
    }

    @Test func carryChipHasNoLinks() {
        let chips = TaskChip.chips(for: task(links: []), carriedLabel: "Jun 2")
        #expect(chips.first?.links.isEmpty == true)
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run the test command above.
Expected: compile failure — `value of type 'TaskChip' has no member 'links'`.

- [ ] **Step 3: Implement the model + derivation**

Replace the entire contents of `Scout/ActionItems/Views/TaskChip.swift` with:

```swift
import Foundation

/// A small source/context chip shown in a task card's collapsed header — the
/// scannable "who/where" line inspired by the triage artifact's chips. Derived
/// purely from a task's deep links and carry marker; no new data.
struct TaskChip: Identifiable, Equatable {
    enum Glyph: Equatable {
        case github, linear, slack, carry
    }

    /// A single click target behind a chip. A chip may summarise several links
    /// (e.g. "2 PRs"); each `Link` becomes a dropdown entry, or — when a chip
    /// has exactly one — the chip opens it directly. A chip with no links
    /// (e.g. "carried Jun 2") renders as static text.
    struct Link: Identifiable, Equatable {
        let label: String
        let url: URL
        var id: String { url.absoluteString }
    }

    let glyph: Glyph
    let label: String
    /// Click targets: 0 = static (no action), 1 = open directly, >1 = dropdown.
    let links: [Link]

    /// `links` is defaulted so call sites and tests that construct a chip by
    /// glyph+label keep compiling and comparing equal.
    init(glyph: Glyph, label: String, links: [Link] = []) {
        self.glyph = glyph
        self.label = label
        self.links = links
    }

    var id: String { "\(label)" }

    /// Derive the chip row for a task: a count/label per deep-link kind (PRs,
    /// Linear, Slack), the repo slug when a single GitHub repo is referenced,
    /// and a "carried <date>" chip when the task was carried in from a prior
    /// day. Order is stable: GitHub → Linear → Slack → carry. Each chip carries
    /// the URL(s) it points at via `links`.
    static func chips(for task: ActionTask, carriedLabel: @autoclosure () -> String? = nil) -> [TaskChip] {
        var chips: [TaskChip] = []

        let prs = task.deepLinks.compactMap { link -> (repo: String, link: Link)? in
            if case .githubPR(let repo, _, _) = link {
                return (repo, Link(label: link.displayLabel, url: link.openURL))
            }
            return nil
        }
        if !prs.isEmpty {
            chips.append(TaskChip(
                glyph: .github,
                label: prs.count == 1 ? "1 PR" : "\(prs.count) PRs",
                links: prs.map(\.link)
            ))
            // Surface the repo only when every PR points at the same one; the
            // repo chip opens the repo homepage, distinct from the PR(s).
            let repos = Set(prs.map(\.repo))
            if repos.count == 1, let repo = repos.first {
                let repoLinks = URL(string: "https://github.com/\(repo)")
                    .map { [Link(label: repo, url: $0)] } ?? []
                chips.append(TaskChip(glyph: .github, label: repo, links: repoLinks))
            }
        }

        let linearLinks = task.deepLinks.compactMap { link -> Link? in
            if case .linear = link { return Link(label: link.displayLabel, url: link.openURL) }
            return nil
        }
        if !linearLinks.isEmpty {
            chips.append(TaskChip(
                glyph: .linear,
                label: linearLinks.count == 1 ? "Linear" : "\(linearLinks.count) Linear",
                links: linearLinks
            ))
        }

        let slackLinks = task.deepLinks.compactMap { link -> Link? in
            if case .slackThread = link { return Link(label: link.displayLabel, url: link.openURL) }
            return nil
        }
        if !slackLinks.isEmpty {
            chips.append(TaskChip(
                glyph: .slack,
                label: slackLinks.count == 1 ? "Slack" : "\(slackLinks.count) Slack",
                links: slackLinks
            ))
        }

        if let carried = carriedLabel() {
            chips.append(TaskChip(glyph: .carry, label: "carried \(carried)"))
        }

        return chips
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run the test command above.
Expected: PASS — all tests in `TaskChipTests` green, including the pre-existing `singleAndPluralPRCounts`, `surfacesRepoOnlyWhenSingleRepo`, `linearAndSlackChips`, `carryChipAppended`, and `stableOrderGitHubLinearSlackCarry`.

- [ ] **Step 5: Commit**

```bash
git add Scout/ActionItems/Views/TaskChip.swift ScoutTests/ActionItems/TaskChipTests.swift
git commit -m "feat(action-items): attach click targets to summary chips"
```

---

## Task 2: Render the chip row as clickable buttons / dropdowns

**Files:**
- Modify: `Scout/ActionItems/Views/TaskCardView.swift` (add `import AppKit`; replace `chipRow`, lines ~139–155)

- [ ] **Step 1: Add the AppKit import**

At the top of `Scout/ActionItems/Views/TaskCardView.swift`, change:

```swift
import SwiftUI
```

to:

```swift
import SwiftUI
import AppKit
```

- [ ] **Step 2: Replace `chipRow` with branching rendering**

Replace this existing block in `Scout/ActionItems/Views/TaskCardView.swift`:

```swift
    private var chipRow: some View {
        HStack(spacing: 6) {
            ForEach(chips) { chip in
                HStack(spacing: 4) {
                    Image(systemName: chipGlyph(chip.glyph))
                        .font(.system(size: 9))
                    Text(chip.label)
                        .font(DS.mono(10.5))
                        .lineLimit(1)
                }
                .foregroundStyle(DS.Ink.p3)
                .padding(.horizontal, 7)
                .padding(.vertical, 2)
                .background(EditorialChipBackground())
            }
        }
    }
```

with:

```swift
    private var chipRow: some View {
        HStack(spacing: 6) {
            ForEach(chips) { chip in
                chipView(for: chip)
            }
        }
    }

    /// Renders a chip per its targets: static text (0 links), a button that
    /// opens directly (1 link), or a dropdown listing each target (>1 links).
    @ViewBuilder
    private func chipView(for chip: TaskChip) -> some View {
        switch chip.links.count {
        case 0:
            chipBody(for: chip)
        case 1:
            Button {
                NSWorkspace.shared.open(chip.links[0].url)
            } label: {
                chipBody(for: chip)
            }
            .buttonStyle(.plainHit)
            .help(chip.links[0].url.absoluteString)
            .onHover { hovering in
                if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
            }
        default:
            Menu {
                ForEach(chip.links) { link in
                    Button(link.label) { NSWorkspace.shared.open(link.url) }
                }
            } label: {
                chipBody(for: chip)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .onHover { hovering in
                if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
            }
        }
    }

    private func chipBody(for chip: TaskChip) -> some View {
        HStack(spacing: 4) {
            Image(systemName: chipGlyph(chip.glyph))
                .font(.system(size: 9))
            Text(chip.label)
                .font(DS.mono(10.5))
                .lineLimit(1)
        }
        .foregroundStyle(DS.Ink.p3)
        .padding(.horizontal, 7)
        .padding(.vertical, 2)
        .background(EditorialChipBackground())
    }
```

- [ ] **Step 3: Build to verify it compiles**

Run the build command above.
Expected: `** BUILD SUCCEEDED **`. If `.menuStyle(.borderlessButton)` leaves visible default chrome around the chip, swap it for `.menuStyle(.button)` plus `.buttonStyle(.plainHit)` on the `Menu` (both valid on this macOS target) and rebuild.

- [ ] **Step 4: Run the app and verify behaviour**

Launch the built app (or run from Xcode). In the Action Items List view, on a collapsed card:
- A single-item chip (`1 PR`, `Linear`, `Slack`) opens its target in the browser on click; cursor turns to a pointing hand on hover.
- The repo chip (e.g. `keboola/crm`) opens `https://github.com/keboola/crm`.
- A multi-item chip (`2 PRs`) shows a dropdown listing each PR; selecting one opens it.
- The `carried …` chip is not clickable and shows no hover cursor.
- Existing affordances (blue inline `#925`, expanded `TaskLinksView`) still work.

- [ ] **Step 5: Run the full test suite**

```bash
DEVELOPER_DIR=/Applications/Xcode-26.5.0.app/Contents/Developer xcodebuild test \
  -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' \
  -only-testing:ScoutTests \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""
```

Expected: PASS — no regressions across `ScoutTests`.

- [ ] **Step 6: Commit**

```bash
git add Scout/ActionItems/Views/TaskCardView.swift
git commit -m "feat(action-items): make List-view summary chips clickable"
```

---

## Self-Review notes

- **Spec coverage:** Task 1 covers the model/derivation and the repo-homepage + multi-item-link decisions; Task 2 covers single-open / dropdown / static rendering and the carry chip staying static. Board view explicitly out of scope per spec.
- **Type consistency:** `TaskChip.Link` (with `label`, `url`, `id`) is defined in Task 1 and consumed unchanged in Task 2 (`chip.links[0].url`, `link.label`, `link.url`). `chipBody(for:)` / `chipView(for:)` names are used consistently.
- **No placeholders:** every code and command step is concrete.
