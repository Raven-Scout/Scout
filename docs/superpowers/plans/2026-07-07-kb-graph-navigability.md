# KB Graph Navigability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unfiltered 272-node global KB map with a bounded focus + context map: a top-hub seed, re-root on tap, name search, and entity-type / hide-orphan / min-degree filters — never more than ~40 nodes on screen.

**Architecture:** Two pure transforms on `KBGraph` (`topHubs`, `filtered`) do the reduction; a thin `KnowledgeBaseService.hubGraph` wrapper seeds the default view and re-rooting reuses the existing `localGraph(around:depth:maxNodes:)`. A new `KBMapView` owns the interactive state (focus, history, search, filters) and drives the unchanged `KBGraphCanvas`; `KBOverviewView` embeds it in place of the old inline map.

**Tech Stack:** Swift, SwiftUI, Grape (existing dep); Swift Testing (`import Testing`, `@Suite`/`@Test`/`#expect`). Synchronized file groups — new `.swift` files under `Scout/`/`ScoutTests/` compile automatically (no `.pbxproj` edit).

## Global Constraints

- **Bounded by construction:** every rendered graph is ≤ 40 nodes regardless of vault size. No full-vault force simulation.
- **Reuse the existing engine:** `undirectedEdges()`, `localGraph(around:depth:maxNodes:)`, `KBGraphCanvas`, `KBEntityGroup`, `KBGraphLegend`. No changes to `KBGraphCanvas` or the right-panel local graph.
- **No silent truncation:** show "showing N of M notes" whenever the rendered set is capped/filtered.
- **The focused center is never filtered out** — `filtered` always retains `isCenter` nodes.
- **No real identifiers in tests** (repo `CLAUDE.md`): neutral stand-ins.
- **Test framework:** Swift Testing only. Run the whole `ScoutTests` target for a reliable verdict; a `-only-testing:ScoutTests/<StructName>` selector may be used for speed only if the run reports "Executed N tests" with N > 0.
- **Platform:** macOS 13+; build/test destination `platform=macOS`.

---

## File Structure

- **Modify** `Scout/KnowledgeBase/Models/KBGraph.swift` — add `KBGraph.topHubs(maxNodes:)` and `KBGraph.filtered(types:hideOrphans:minDegree:)` (pure).
- **Modify** `Scout/KnowledgeBase/KnowledgeBaseService.swift` — add `hubGraph(maxNodes:)` wrapper.
- **Create** `Scout/KnowledgeBase/Views/KBMapView.swift` — the interactive focus+context map (state, search, filters, breadcrumb, canvas).
- **Modify** `Scout/KnowledgeBase/Views/KBOverviewView.swift` — embed `KBMapView` in place of the inline `fullGraph()` map.
- **Create** `ScoutTests/KnowledgeBase/KBGraphTransformTests.swift` — unit tests for the transforms + `hubGraph`.

---

## Task 1: Graph transforms (`topHubs`, `filtered`) + `hubGraph` seed

**Files:**
- Modify: `Scout/KnowledgeBase/Models/KBGraph.swift`
- Modify: `Scout/KnowledgeBase/KnowledgeBaseService.swift` (after `fullGraph()`, ~line 346)
- Test: `ScoutTests/KnowledgeBase/KBGraphTransformTests.swift`

**Interfaces:**
- Consumes: `KBGraph`, `KBGraphNode`, `KBGraphEdge`, `KBEntityGroup` (existing); `KnowledgeBaseService.fullGraph()` (existing).
- Produces:
  - `KBGraph.topHubs(maxNodes: Int) -> KBGraph` — highest-degree nodes (degree desc, id asc tiebreak), only edges internal to the kept set. Returns `self` when `nodes.count <= maxNodes`.
  - `KBGraph.filtered(types: Set<KBEntityGroup>, hideOrphans: Bool, minDegree: Int) -> KBGraph` — keeps nodes in `types` with `degree >= minDegree` (and, if `hideOrphans`, `degree > 0`); **always keeps `isCenter` nodes**; drops edges with a removed endpoint.
  - `KnowledgeBaseService.hubGraph(maxNodes: Int = 40) -> KBGraph` — `fullGraph().topHubs(maxNodes:)`.

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/KnowledgeBase/KBGraphTransformTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("KBGraph transforms")
struct KBGraphTransformTests {
    private func node(_ id: String, degree: Int, group: KBEntityGroup = .other, center: Bool = false) -> KBGraphNode {
        KBGraphNode(id: id, label: id, group: group, degree: degree, isCenter: center)
    }

    // topHubs

    @Test func topHubsKeepsHighestDegreeAndInternalEdges() {
        let g = KBGraph(
            nodes: [node("a", degree: 3), node("b", degree: 2), node("c", degree: 1), node("d", degree: 0)],
            edges: [KBGraphEdge(from: "a", to: "b"), KBGraphEdge(from: "b", to: "c"), KBGraphEdge(from: "c", to: "d")]
        )
        let hubs = g.topHubs(maxNodes: 2)
        #expect(hubs.nodes.map(\.id) == ["a", "b"])            // top 2 by degree
        #expect(hubs.edges == [KBGraphEdge(from: "a", to: "b")]) // only the internal edge
    }

    @Test func topHubsIsNoOpWhenUnderCap() {
        let g = KBGraph(nodes: [node("a", degree: 1), node("b", degree: 1)],
                        edges: [KBGraphEdge(from: "a", to: "b")])
        #expect(g.topHubs(maxNodes: 5) == g)
    }

    @Test func topHubsBreaksTiesByIdForDeterminism() {
        let g = KBGraph(nodes: [node("z", degree: 1), node("a", degree: 1), node("m", degree: 1)], edges: [])
        #expect(g.topHubs(maxNodes: 2).nodes.map(\.id) == ["a", "m"])  // equal degree → id asc
    }

    // filtered

    @Test func filteredDropsTypesDegreeAndOrphans() {
        let g = KBGraph(
            nodes: [node("p", degree: 2, group: .people), node("j", degree: 1, group: .issues),
                    node("o", degree: 0, group: .people)],
            edges: [KBGraphEdge(from: "p", to: "j")]
        )
        let out = g.filtered(types: [.people], hideOrphans: true, minDegree: 1)
        #expect(out.nodes.map(\.id) == ["p"])       // j wrong type; o orphan
        #expect(out.edges.isEmpty)                   // p–j edge drops with j
    }

    @Test func filteredAlwaysKeepsCenter() {
        let g = KBGraph(
            nodes: [node("c", degree: 0, group: .issues, center: true), node("x", degree: 5, group: .people)],
            edges: []
        )
        // Center is an orphan of a de-selected type below minDegree, yet retained.
        let out = g.filtered(types: [.people], hideOrphans: true, minDegree: 3)
        #expect(out.nodes.contains { $0.id == "c" })
        #expect(out.nodes.contains { $0.id == "x" })
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBGraphTransformTests 2>&1 | tail -20`
Expected: compile failure — `topHubs`/`filtered` don't exist.

- [ ] **Step 3: Implement the transforms**

In `Scout/KnowledgeBase/Models/KBGraph.swift`, after the `KBGraph` struct (line 100), add:

```swift
extension KBGraph {
    /// The top `maxNodes` nodes by degree (ties broken by id ascending for a
    /// stable layout), plus only the edges whose endpoints are both kept.
    /// Returns `self` unchanged when already within the cap. Seeds the overview
    /// with the vault's most-connected "spine" instead of all N notes.
    func topHubs(maxNodes: Int) -> KBGraph {
        guard nodes.count > maxNodes else { return self }
        let kept = nodes
            .sorted { a, b in a.degree != b.degree ? a.degree > b.degree : a.id < b.id }
            .prefix(maxNodes)
        let keptIds = Set(kept.map(\.id))
        return KBGraph(nodes: Array(kept),
                       edges: edges.filter { keptIds.contains($0.from) && keptIds.contains($0.to) })
    }

    /// Keep nodes in `types`, with `degree >= minDegree`, and (when
    /// `hideOrphans`) `degree > 0`. A center node is ALWAYS kept so a re-rooted
    /// view is never emptied by a filter. Edges with a removed endpoint drop.
    func filtered(types: Set<KBEntityGroup>, hideOrphans: Bool, minDegree: Int) -> KBGraph {
        let keptNodes = nodes.filter { n in
            if n.isCenter { return true }
            guard types.contains(n.group) else { return false }
            if n.degree < minDegree { return false }
            if hideOrphans && n.degree == 0 { return false }
            return true
        }
        let keptIds = Set(keptNodes.map(\.id))
        return KBGraph(nodes: keptNodes,
                       edges: edges.filter { keptIds.contains($0.from) && keptIds.contains($0.to) })
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBGraphTransformTests 2>&1 | tail -20`
Expected: PASS — "Executed 5 tests, with 0 failures" (confirm N = 5).

- [ ] **Step 5: Add the `hubGraph` seed wrapper**

In `Scout/KnowledgeBase/KnowledgeBaseService.swift`, right after `fullGraph()` (ends ~line 346), add:

```swift
    /// The overview's default seed: the most-connected notes across the whole
    /// KB, bounded so the map opens readable rather than as a full-vault
    /// hairball. Degree/grouping come from `fullGraph()`.
    func hubGraph(maxNodes: Int = 40) -> KBGraph {
        fullGraph().topHubs(maxNodes: maxNodes)
    }
```

- [ ] **Step 6: Verify the wrapper against a real tree (fixture test)**

Append to `KBGraphTransformTests.swift` a fixture suite mirroring the existing `KnowledgeBaseService full graph` test:

```swift
@Suite("KnowledgeBaseService hubGraph")
struct KnowledgeBaseServiceHubGraphTests {
    /// Minimal on-disk KB: a hub linked by three leaves.
    private func makeHubKB() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbhub-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        // hub links to alex, priya, sam; leaves link only back to hub.
        try "[[alex]] [[priya]] [[sam]]".write(to: kb.appendingPathComponent("hub.md"), atomically: true, encoding: .utf8)
        try "[[hub]]".write(to: kb.appendingPathComponent("alex.md"), atomically: true, encoding: .utf8)
        try "[[hub]]".write(to: kb.appendingPathComponent("priya.md"), atomically: true, encoding: .utf8)
        try "[[hub]]".write(to: kb.appendingPathComponent("sam.md"), atomically: true, encoding: .utf8)
        return root
    }

    @Test func hubGraphCapsAndKeepsTheHub() async throws {
        let root = try makeHubKB()
        defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        try await svc.reparseAndWait()

        let g = svc.hubGraph(maxNodes: 2)
        #expect(g.nodes.count == 2)
        #expect(g.nodes.contains { $0.id == "knowledge-base/hub.md" })  // highest degree kept
    }
}
```

Note: `NoopFS` and `reparseAndWait()` are the same helpers the existing `KnowledgeBaseService graph`/`full graph` suites use (see `KnowledgeBaseTests.swift`). If the display-path form differs, assert on `g.nodes.first!.degree` being the max instead.

- [ ] **Step 7: Run the transform + fixture tests**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBGraphTransformTests -only-testing:ScoutTests/KnowledgeBaseServiceHubGraphTests 2>&1 | tail -20`
Expected: PASS — "Executed 6 tests, with 0 failures".

- [ ] **Step 8: Commit**

```bash
git add Scout/KnowledgeBase/Models/KBGraph.swift Scout/KnowledgeBase/KnowledgeBaseService.swift ScoutTests/KnowledgeBase/KBGraphTransformTests.swift
git commit -m "feat(kb): graph topHubs/filtered transforms + hubGraph seed"
```

---

## Task 2: `KBMapView` — hub seed, re-root, breadcrumb, search

**Files:**
- Create: `Scout/KnowledgeBase/Views/KBMapView.swift`
- Modify: `Scout/KnowledgeBase/Views/KBOverviewView.swift:36,73-86`

**Interfaces:**
- Consumes: `KnowledgeBaseService.hubGraph`, `localGraph`, `graphStats`, `tree` (existing); `KBGraphCanvas`, `KBGraphLegend` (existing); `KBGraph.filtered` (Task 1 — used in Task 3, imported here as a no-op default: all types, no orphan hide, minDegree 0).
- Produces: `KBMapView(service:onOpen:)` — a self-contained interactive map. `onOpen(String)` opens a note in the editor.

**No unit test** (no view tests in this project); verified by build + manual `/run`. Interaction: **single-tap a non-center node re-roots on it; single-tap the current center opens it**; an explicit "Open ›" button in the breadcrumb also opens. (Grape exposes only a single-tap gesture, so "open" is the second tap / the button rather than a double-click — a refinement of the spec's "double-click to open".)

- [ ] **Step 1: Create `KBMapView` with seed + re-root + breadcrumb + search**

Create `Scout/KnowledgeBase/Views/KBMapView.swift`:

```swift
import SwiftUI

/// The overview's interactive focus + context map. Opens on the vault's top
/// hubs; single-tap a node to re-root on its neighbourhood, tap the centre
/// again (or the "Open" button) to open it. Bounded to ~40 nodes at all times.
struct KBMapView: View {
    @ObservedObject var service: KnowledgeBaseService
    /// Open a note in the editor.
    let onOpen: (String) -> Void

    private let maxNodes = 40

    @State private var focusPath: String? = nil
    @State private var history: [String] = []
    @State private var query: String = ""

    // Filters (wired in Task 3; defaults here render the full base graph).
    @State private var activeTypes: Set<KBEntityGroup> = Set(KBEntityGroup.allCases)
    @State private var hideOrphans = false
    @State private var minDegree = 0

    private var baseGraph: KBGraph {
        if let f = focusPath { return service.localGraph(around: f, depth: 2, maxNodes: maxNodes) }
        return service.hubGraph(maxNodes: maxNodes)
    }
    private var shownGraph: KBGraph {
        baseGraph.filtered(types: activeTypes, hideOrphans: hideOrphans, minDegree: minDegree)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("MAP").font(DS.sans(10, weight: .semibold)).tracking(0.6).foregroundStyle(DS.Ink.p4)
            searchField
            breadcrumb
            canvas
            KBGraphLegend(groups: Array(Set(shownGraph.nodes.map(\.group))).sorted { $0.label < $1.label })
        }
    }

    // MARK: search

    private var matches: [KBNode] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard q.count >= 2 else { return [] }
        return service.tree.flatMap(\.allFiles)
            .filter { $0.ext == "md" && $0.displayName.lowercased().contains(q) }
            .prefix(8).map { $0 }
    }

    private var searchField: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass").font(.system(size: 11)).foregroundStyle(DS.Ink.p4)
                TextField("Focus a note…", text: $query)
                    .textFieldStyle(.plain).font(DS.sans(12)).foregroundStyle(DS.Ink.p1)
                    .onSubmit { if let first = matches.first { focus(first.relativePath) } }
            }
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk))
            if !matches.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(matches, id: \.relativePath) { n in
                        Button { focus(n.relativePath) } label: {
                            HStack {
                                Text(n.displayName).font(DS.sans(12)).foregroundStyle(DS.Ink.p1)
                                Spacer()
                                Text(n.relativePath).font(DS.mono(10)).foregroundStyle(DS.Ink.p4)
                            }
                            .padding(.horizontal, 10).padding(.vertical, 5).contentShape(Rectangle())
                        }.buttonStyle(.plain)
                    }
                }
                .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.base))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
            }
        }
        .frame(maxWidth: 460, alignment: .leading)
    }

    // MARK: breadcrumb

    private var breadcrumb: some View {
        HStack(spacing: 8) {
            Button { toHubs() } label: {
                Label("Hubs", systemImage: "house").font(DS.sans(11))
            }.buttonStyle(.plain).foregroundStyle(focusPath == nil ? DS.Ink.p3 : DS.Accent.ink)
            if !history.isEmpty || focusPath != nil {
                Button { back() } label: { Label("Back", systemImage: "chevron.left").font(DS.sans(11)) }
                    .buttonStyle(.plain).foregroundStyle(DS.Accent.ink)
            }
            if let f = focusPath {
                Text(KBNode.displayName(forPath: f)).font(DS.sans(11, weight: .semibold)).foregroundStyle(DS.Ink.p1)
                Button { onOpen(f) } label: { Label("Open", systemImage: "arrow.up.right.square").font(DS.sans(11)) }
                    .buttonStyle(.plain).foregroundStyle(DS.Accent.ink)
            }
            Spacer(minLength: 0)
        }
    }

    // MARK: canvas

    private var canvas: some View {
        KBGraphCanvas(graph: shownGraph, onNavigate: handleTap,
                      labelMinDegree: focusPath == nil ? 3 : 1, initialScale: 2.0)
            .id("\(focusPath ?? "hubs")|\(activeTypes.count)|\(hideOrphans)|\(minDegree)")
            .frame(height: 460).frame(maxWidth: 1100)
            .background(RoundedRectangle(cornerRadius: 8).fill(DS.Paper.sunk.opacity(0.4)))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
    }

    // MARK: actions

    private func handleTap(_ id: String) {
        if id == focusPath { onOpen(id); return }   // tap the centre again → open
        focus(id)
    }
    private func focus(_ path: String) {
        if let current = focusPath { history.append(current) }
        focusPath = path
        query = ""
    }
    private func back() {
        focusPath = history.popLast()
    }
    private func toHubs() {
        focusPath = nil; history.removeAll()
    }
}
```

- [ ] **Step 2: Embed `KBMapView` in `KBOverviewView`**

In `KBOverviewView.swift`, delete the `let kbGraph = service.fullGraph()` line (line 36).

Replace the map block (lines 73-86, the `if kbGraph.edges.count > 0 { VStack … }`) with:

```swift
                KBMapView(service: service, onOpen: onNavigate)
```

- [ ] **Step 3: Build**

Run: `xcodebuild -scheme Scout -configuration Debug -destination 'platform=macOS' build 2>&1 | tail -15`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 4: Run the full test target (nothing regressed)**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' 2>&1 | tail -15`
Expected: PASS — "Executed N tests, with 0 failures".

- [ ] **Step 5: Manual verification (drive the app)**

Use `/run` (Debug "Scout Dev"). On the KB tab with no note selected (overview):
1. The map opens showing ~40 top-hub nodes, not all 272.
2. Tap a node → the map re-roots on it (it becomes the centre with its neighbourhood); the breadcrumb shows its name + Open.
3. Tap the centred node again → the note opens in the editor. Also verify the breadcrumb "Open" button opens it.
4. "‹ Back" returns to the previous root; "⌂ Hubs" returns to the seed.
5. Type ≥2 chars in the search field → matching notes list → click one → map re-roots on it.

- [ ] **Step 6: Commit**

```bash
git add Scout/KnowledgeBase/Views/KBMapView.swift Scout/KnowledgeBase/Views/KBOverviewView.swift
git commit -m "feat(kb): interactive focus+context map — hub seed, re-root, search, breadcrumb"
```

---

## Task 3: Filters — entity types, hide orphans, min degree, honest cap

**Files:**
- Modify: `Scout/KnowledgeBase/Views/KBMapView.swift`

**Interfaces:**
- Consumes: `KBGraph.filtered` (Task 1); the `@State` filter properties already declared in Task 2.
- Produces: filter bar + cap caption + empty state in `KBMapView`.

**No unit test** (the filter logic is unit-tested in Task 1; this wires it to controls). Verified by build + manual `/run`.

- [ ] **Step 1: Add the filter bar, cap caption, and empty state to `KBMapView.body`**

In `KBMapView`, insert `filterBar` after `breadcrumb` and wrap the canvas with the caption + empty state. Replace the `canvas` line and legend in `body` with:

```swift
            filterBar
            if shownGraph.nodes.isEmpty {
                noMatchState
            } else {
                canvas
                capCaption
                KBGraphLegend(groups: Array(Set(shownGraph.nodes.map(\.group))).sorted { $0.label < $1.label })
            }
```

- [ ] **Step 2: Add the filter controls, caption, and empty state**

Add these members to `KBMapView`:

```swift
    private var filterBar: some View {
        FlowLayout(spacing: 8) {
            ForEach(KBEntityGroup.allCases, id: \.self) { g in
                let on = activeTypes.contains(g)
                Button {
                    if on { activeTypes.remove(g) } else { activeTypes.insert(g) }
                } label: {
                    HStack(spacing: 4) {
                        Circle().fill(g.color).frame(width: 7, height: 7)
                        Text(g.label).font(DS.sans(10.5))
                    }
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(Capsule().fill(on ? DS.Accent.wash : DS.Paper.sunk))
                    .overlay(Capsule().strokeBorder(on ? DS.Accent.fill.opacity(0.5) : .clear, lineWidth: 1))
                    .foregroundStyle(on ? DS.Ink.p1 : DS.Ink.p4)
                }.buttonStyle(.plain)
            }
            Toggle("Hide orphans", isOn: $hideOrphans)
                .toggleStyle(.checkbox).font(DS.sans(10.5)).foregroundStyle(DS.Ink.p3)
            HStack(spacing: 4) {
                Text("min links").font(DS.sans(10.5)).foregroundStyle(DS.Ink.p4)
                Stepper(value: $minDegree, in: 0...10) {
                    Text("\(minDegree)").font(DS.mono(11)).foregroundStyle(DS.Ink.p2)
                }.labelsHidden()
            }
        }
        .frame(maxWidth: 1100, alignment: .leading)
    }

    private var capCaption: some View {
        Text("showing \(shownGraph.nodes.count) of \(service.graphStats().notes) notes")
            .font(DS.sans(10)).foregroundStyle(DS.Ink.p4)
    }

    private var noMatchState: some View {
        VStack(spacing: 8) {
            Image(systemName: "line.3.horizontal.decrease.circle").font(.system(size: 22)).foregroundStyle(DS.Ink.p4)
            Text("No notes match these filters").font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
            Button("Reset filters") {
                activeTypes = Set(KBEntityGroup.allCases); hideOrphans = false; minDegree = 0
            }.buttonStyle(.plain).font(DS.sans(11, weight: .semibold)).foregroundStyle(DS.Accent.ink)
        }
        .frame(maxWidth: .infinity).frame(height: 200)
    }
```

- [ ] **Step 3: Build**

Run: `xcodebuild -scheme Scout -configuration Debug -destination 'platform=macOS' build 2>&1 | tail -15`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 4: Run the full test target**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' 2>&1 | tail -15`
Expected: PASS — "Executed N tests, with 0 failures".

- [ ] **Step 5: Manual verification**

Via `/run`, on the overview map:
1. Toggle an entity-type chip off → nodes of that type disappear; toggle on → return.
2. "Hide orphans" → degree-0 nodes vanish.
3. Raise "min links" → low-degree nodes drop; the caption "showing N of 272" updates.
4. Filter everything out → the "No notes match these filters" state with a working "Reset filters".
5. Focus a node, then apply a filter that would exclude it → the centre stays (its neighbours may thin).

- [ ] **Step 6: Commit**

```bash
git add Scout/KnowledgeBase/Views/KBMapView.swift
git commit -m "feat(kb): map filters — entity types, hide orphans, min-degree, honest cap caption"
```

---

## Self-Review

**1. Spec coverage** (against `2026-07-06-kb-graph-navigability-design.md`):
- `hubGraph(40)` seed → Task 1. ✓
- Re-root reuses `localGraph(…maxNodes:40)` → Task 2 `baseGraph`. ✓
- `filter(types:hideOrphans:minDegree:)`, center always retained → Task 1 (`filtered`), tested. ✓
- Search → focus → Task 2 `searchField`/`focus`. ✓
- Tap = re-root, open via second tap + Open button (spec's double-click adapted to Grape's single-tap gesture — noted) → Task 2 `handleTap`. ✓
- Breadcrumb ‹ back / ⌂ hubs → Task 2. ✓
- Entity-type + hide-orphan + min-degree filters → Task 3. ✓
- Honest "showing N of M" caption; no-match empty state → Task 3. ✓
- Bounded ≤ 40 nodes → `topHubs`/`localGraph` caps. ✓
- Local graph untouched, `KBGraphCanvas` unchanged → confirmed (map passes its own `onNavigate` handler). ✓
- Deleted/renamed focus note → `localGraph` on a missing path returns just that (empty-ish) graph; a later reparse + user pressing ⌂ Hubs recovers. (Minor: no auto-reset; acceptable — noted below.)

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to"; every code step is complete.

**3. Type consistency:** `topHubs(maxNodes:)`, `filtered(types:hideOrphans:minDegree:)`, `hubGraph(maxNodes:)`, `KBMapView(service:onOpen:)`, `handleTap`, `focus`, `baseGraph`/`shownGraph` — names consistent across tasks. `KBGraphCanvas` init used exactly as defined (`graph`, `onNavigate`, `labelMinDegree`, `initialScale`).

## Notes for the implementer

- **Single-tap vs double-click:** Grape's overlay exposes a single tap (`withGraphTapGesture`). This plan implements "open" as tapping the already-centred node **plus** an explicit "Open" button in the breadcrumb, rather than a double-click. If a later Grape version exposes a count/double gesture, the double-click variant from the spec can replace `handleTap`'s centre check.
- **Graph recomputation:** `baseGraph`/`shownGraph` recompute per `body` eval (as the old `KBOverviewView` already did with `fullGraph()`); each is bounded work over the in-memory index. If profiling shows churn on filter toggles, memoize `baseGraph` by `focusPath` with a small cache like `KBEditableView.SegmentCache`.
- **Stale focus after delete/rename:** if the focused note is removed on disk, `localGraph` returns a near-empty graph; the user recovers via ⌂ Hubs. A follow-up could reset `focusPath` to nil when it no longer resolves in `service.tree`.
```

