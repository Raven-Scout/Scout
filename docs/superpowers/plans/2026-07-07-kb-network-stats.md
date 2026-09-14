# KB Network-Analysis Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the overview's single "N notes · M connections" line into a NETWORK section with a **Vault Health** block (orphans, weakly-linked, dangling links, disconnected islands — clickable) and an **Insight** block (top hubs, degree summary, per-type breakdown, components), all from one in-memory pass.

**Architecture:** A new `networkStats() -> KBNetworkStats` on `KnowledgeBaseService` computes every metric in a single traversal of the existing index + `undirectedEdges()` adjacency (no new I/O). A new `KBStatsView` renders it — health as count + top-5 + "show all" disclosures, insight as a compact summary — and `KBOverviewView` embeds it.

**Tech Stack:** Swift, SwiftUI; Swift Testing. Synchronized file groups (no `.pbxproj` edits).

## Global Constraints

- **Stacks on feature 2** (PR #73). This plan assumes feature 2 is merged: `KBOverviewView` already embeds `KBMapView`, and `KBGraph`/degree helpers exist. Base this branch on feature-2 code, not bare `main`.
- **No new data source / disk I/O:** compute only from `index` (`outByFile`, `stemToPath`, `typeByFile`), `tree.allFiles`, and `undirectedEdges()`.
- **Definitions (exact):** orphan = degree 0; weakly-linked = degree exactly 1; dangling = an outgoing `[[target]]` with no `stemToPath` resolution; island = a connected component of size ≥ 2 that is **not** the largest component; hub = a note ranked by degree (degree > 0), degree desc / path asc.
- **Compact by default:** each list shows count + top 5, with a "show all N" disclosure. Empty metric → "✓ none".
- **Determinism:** all output lists are sorted (paths asc; hubs by degree-then-path) so the UI is stable across reparses.
- **No real identifiers in tests** (repo `CLAUDE.md`).
- **Test framework:** Swift Testing; run the whole `ScoutTests` target for a reliable verdict (a `-only-testing:ScoutTests/<StructName>` selector is fine for speed only if it reports "Executed N tests", N > 0).
- **Platform:** macOS 13+; destination `platform=macOS`.

---

## File Structure

- **Modify** `Scout/KnowledgeBase/Models/KBGraph.swift` — add `KBNetworkStats` + `KBDanglingLink`, `KBHub`, `KBTypeCount`.
- **Modify** `Scout/KnowledgeBase/KnowledgeBaseService.swift` — add `networkStats(hubCap:)`.
- **Create** `Scout/KnowledgeBase/Views/KBStatsView.swift` — the NETWORK section (health + insight).
- **Modify** `Scout/KnowledgeBase/Views/KBOverviewView.swift` — embed `KBStatsView`.
- **Create** `ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift` — fixture-based unit tests.

---

## Task 1: `KBNetworkStats` model + `networkStats()` engine

**Files:**
- Modify: `Scout/KnowledgeBase/Models/KBGraph.swift` (after `KBIndex`, ~line 113)
- Modify: `Scout/KnowledgeBase/KnowledgeBaseService.swift` (after `hubGraph`/`fullGraph`, ~line 352)
- Test: `ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift`

**Interfaces:**
- Consumes: `undirectedEdges()`, `index.outByFile`/`stemToPath`/`typeByFile`, `tree.allFiles`, `KBEntityGroup.of`, `KBGraphEdge` (existing).
- Produces:
  - `KBNetworkStats` with fields: `orphans:[String]`, `weaklyLinked:[String]`, `dangling:[KBDanglingLink]`, `islands:[[String]]`, `topHubs:[KBHub]`, `avgDegree:Double`, `maxDegree:Int`, `byType:[KBTypeCount]`, `clusterCount:Int`, `largestComponentSize:Int`; `.empty`.
  - `KBDanglingLink(source:String,target:String)`, `KBHub(path:String,degree:Int)`, `KBTypeCount(group:KBEntityGroup,count:Int)` — all `Identifiable`.
  - `KnowledgeBaseService.networkStats(hubCap: Int = 20) -> KBNetworkStats`.

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("KnowledgeBaseService networkStats")
struct KBNetworkStatsTests {
    /// Fixture: a 4-note mainland (hub + 3 leaves), a 2-note island, one orphan,
    /// and one dangling link (sam → ghost, which doesn't exist).
    private func makeStatsKB() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbstats-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        func w(_ name: String, _ body: String) throws {
            try body.write(to: kb.appendingPathComponent(name), atomically: true, encoding: .utf8)
        }
        try w("hub.md", "[[alex]] [[priya]] [[sam]]")
        try w("alex.md", "[[hub]]")
        try w("priya.md", "[[hub]]")
        try w("sam.md", "[[hub]] [[ghost]]")          // ghost is dangling
        try w("island-a.md", "[[island-b]]")
        try w("island-b.md", "[[island-a]]")
        try w("lonely.md", "no links here")            // orphan
        return root
    }

    private func stats() async throws -> (URL, KBNetworkStats) {
        let root = try makeStatsKB()
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        try await svc.reparseAndWait()
        return (root, svc.networkStats())
    }

    @Test func orphansAndWeaklyLinked() async throws {
        let (root, s) = try await stats(); defer { try? FileManager.default.removeItem(at: root) }
        #expect(s.orphans == ["knowledge-base/lonely.md"])
        #expect(Set(s.weaklyLinked) == Set([
            "knowledge-base/alex.md", "knowledge-base/priya.md", "knowledge-base/sam.md",
            "knowledge-base/island-a.md", "knowledge-base/island-b.md",
        ]))
    }

    @Test func danglingLinkDetected() async throws {
        let (root, s) = try await stats(); defer { try? FileManager.default.removeItem(at: root) }
        #expect(s.dangling.count == 1)
        #expect(s.dangling.first?.source == "knowledge-base/sam.md")
        #expect(s.dangling.first?.target == "ghost")
    }

    @Test func islandsExcludeMainlandAndOrphan() async throws {
        let (root, s) = try await stats(); defer { try? FileManager.default.removeItem(at: root) }
        #expect(s.islands.count == 1)                                  // only the 2-note island
        #expect(Set(s.islands[0]) == Set(["knowledge-base/island-a.md", "knowledge-base/island-b.md"]))
        #expect(s.largestComponentSize == 4)                            // hub + 3 leaves
        #expect(s.clusterCount == 2)                                    // mainland + island
    }

    @Test func hubsAndDegreeSummary() async throws {
        let (root, s) = try await stats(); defer { try? FileManager.default.removeItem(at: root) }
        #expect(s.topHubs.first?.path == "knowledge-base/hub.md")
        #expect(s.topHubs.first?.degree == 3)
        #expect(s.maxDegree == 3)
        #expect(!s.topHubs.contains { $0.degree == 0 })                 // orphans aren't hubs
    }

    @Test func byTypeCountsAllNotes() async throws {
        let (root, s) = try await stats(); defer { try? FileManager.default.removeItem(at: root) }
        #expect(s.byType.reduce(0) { $0 + $1.count } == 7)              // 7 md notes total
        #expect(s.byType.count == KBEntityGroup.allCases.count)         // every group present (0s incl.)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBNetworkStatsTests 2>&1 | tail -20`
Expected: compile failure — `networkStats` / `KBNetworkStats` don't exist.

- [ ] **Step 3: Add the model types**

In `Scout/KnowledgeBase/Models/KBGraph.swift`, after `KBIndex` (line 113), add:

```swift
// MARK: - Network stats

nonisolated struct KBDanglingLink: Identifiable, Equatable {
    let source: String     // note holding the broken link (relative path)
    let target: String     // unresolved [[target]] text
    var id: String { source + "→" + target }
}

nonisolated struct KBHub: Identifiable, Equatable {
    let path: String
    let degree: Int
    var id: String { path }
}

nonisolated struct KBTypeCount: Identifiable, Equatable {
    let group: KBEntityGroup
    let count: Int
    var id: KBEntityGroup { group }
}

/// High-level network analysis of the whole KB: actionable health signals plus
/// read-only connectivity insight. Computed in one pass over the index + edges.
nonisolated struct KBNetworkStats: Equatable {
    let orphans: [String]                // degree 0
    let weaklyLinked: [String]           // degree exactly 1
    let dangling: [KBDanglingLink]       // outgoing [[target]] with no resolution
    let islands: [[String]]              // components of size >= 2, excluding the largest
    let topHubs: [KBHub]                 // degree desc, path asc; degree > 0; capped
    let avgDegree: Double
    let maxDegree: Int
    let byType: [KBTypeCount]            // count per KBEntityGroup (all groups, 0s included)
    let clusterCount: Int                // number of components with size >= 2
    let largestComponentSize: Int
    static let empty = KBNetworkStats(orphans: [], weaklyLinked: [], dangling: [], islands: [],
                                      topHubs: [], avgDegree: 0, maxDegree: 0, byType: [],
                                      clusterCount: 0, largestComponentSize: 0)
}
```

- [ ] **Step 4: Implement `networkStats()`**

In `Scout/KnowledgeBase/KnowledgeBaseService.swift`, after `hubGraph(maxNodes:)` (added in feature 2), add:

```swift
    /// One-pass network analysis for the overview: orphans / weakly-linked /
    /// dangling / islands (health) + hubs / degree / per-type / components
    /// (insight). Reads only the in-memory index + edges (no disk I/O).
    func networkStats(hubCap: Int = 20) -> KBNetworkStats {
        let notes = tree.flatMap(\.allFiles).filter { $0.ext == "md" }.map(\.relativePath)
        guard !notes.isEmpty else { return .empty }
        let edgeSet = undirectedEdges()

        // Adjacency across all existing notes (degree-0 notes present with []).
        var adj: [String: Set<String>] = [:]
        for n in notes { adj[n] = [] }
        for e in edgeSet {
            adj[e.from, default: []].insert(e.to)
            adj[e.to, default: []].insert(e.from)
        }
        let degree: (String) -> Int = { adj[$0]?.count ?? 0 }

        // Health: orphans / weakly-linked.
        let orphans = notes.filter { degree($0) == 0 }.sorted()
        let weaklyLinked = notes.filter { degree($0) == 1 }.sorted()

        // Health: dangling links (outgoing target that doesn't resolve).
        var dangling: [KBDanglingLink] = []
        for (source, targets) in index.outByFile {
            for t in targets where index.stemToPath[t.lowercased()] == nil {
                dangling.append(KBDanglingLink(source: source, target: t))
            }
        }
        dangling.sort { $0.source != $1.source ? $0.source < $1.source : $0.target < $1.target }

        // Connected components (iterative DFS over adjacency).
        var seen = Set<String>()
        var components: [[String]] = []
        for start in notes where !seen.contains(start) {
            var comp: [String] = []; var stack = [start]; seen.insert(start)
            while let node = stack.popLast() {
                comp.append(node)
                for nb in adj[node] ?? [] where !seen.contains(nb) { seen.insert(nb); stack.append(nb) }
            }
            components.append(comp)
        }
        let byCount = components.sorted { $0.count > $1.count }
        let largestComponentSize = byCount.first?.count ?? 0
        let multi = byCount.filter { $0.count >= 2 }               // clusters
        let islands = Array(multi.dropFirst()).map { $0.sorted() } // all clusters except the largest

        // Insight: degree summary + hubs + per-type.
        let maxDegree = notes.map(degree).max() ?? 0
        let avgDegree = Double(2 * edgeSet.count) / Double(notes.count)
        let topHubs = notes
            .sorted { degree($0) != degree($1) ? degree($0) > degree($1) : $0 < $1 }
            .prefix(hubCap)
            .map { KBHub(path: $0, degree: degree($0)) }
            .filter { $0.degree > 0 }
        var counts: [KBEntityGroup: Int] = [:]
        for n in notes { counts[KBEntityGroup.of(n, type: index.typeByFile[n]), default: 0] += 1 }
        let byType = KBEntityGroup.allCases.map { KBTypeCount(group: $0, count: counts[$0] ?? 0) }

        return KBNetworkStats(
            orphans: orphans, weaklyLinked: weaklyLinked, dangling: dangling, islands: islands,
            topHubs: Array(topHubs), avgDegree: avgDegree, maxDegree: maxDegree, byType: byType,
            clusterCount: multi.count, largestComponentSize: largestComponentSize)
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBNetworkStatsTests 2>&1 | tail -20`
Expected: PASS — "Executed 5 tests, with 0 failures".

- [ ] **Step 6: Commit**

```bash
git add Scout/KnowledgeBase/Models/KBGraph.swift Scout/KnowledgeBase/KnowledgeBaseService.swift ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift
git commit -m "feat(kb): networkStats() — orphans/dangling/islands + hubs/degree/type/components"
```

---

## Task 2: `KBStatsView` — Vault Health block + embed in overview

**Files:**
- Create: `Scout/KnowledgeBase/Views/KBStatsView.swift`
- Modify: `Scout/KnowledgeBase/Views/KBOverviewView.swift`

**Interfaces:**
- Consumes: `KnowledgeBaseService.networkStats()`, `graphStats()` (existing); `KBNetworkStats`, `KBNode.displayName(forPath:)` (existing).
- Produces: `KBStatsView(service:onOpen:)` — `onOpen(String)` opens a note in the editor.

**No unit test** (engine covered in Task 1); build + manual `/run`.

- [ ] **Step 1: Create `KBStatsView` with the header + health block**

Create `Scout/KnowledgeBase/Views/KBStatsView.swift`:

```swift
import SwiftUI

/// The overview's NETWORK section: vault-health problem lists (clickable) plus
/// read-only connectivity insight. All data from `service.networkStats()`.
struct KBStatsView: View {
    @ObservedObject var service: KnowledgeBaseService
    /// Open a note in the editor.
    let onOpen: (String) -> Void

    private let topN = 5

    var body: some View {
        let stats = service.graphStats()
        let net = service.networkStats()
        VStack(alignment: .leading, spacing: 20) {
            Text("\(stats.notes) notes · \(stats.links) connections")
                .font(DS.sans(13)).foregroundStyle(DS.Ink.p3)
            healthBlock(net)
            // insightBlock(net) added in Task 3
        }
    }

    // MARK: - Health

    @ViewBuilder
    private func healthBlock(_ net: KBNetworkStats) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("VAULT HEALTH").font(DS.sans(10, weight: .semibold)).tracking(0.6).foregroundStyle(DS.Ink.p4)
            pathRow("Dangling links", icon: "link.badge.plus",
                    labels: net.dangling.map { "\(KBNode.displayName(forPath: $0.source)) → \($0.target)" },
                    paths: net.dangling.map(\.source))
            pathRow("Orphaned notes", icon: "circle.dashed",
                    labels: net.orphans.map(KBNode.displayName(forPath:)), paths: net.orphans)
            islandsRow(net.islands)
            pathRow("Weakly linked", icon: "link",
                    labels: net.weaklyLinked.map(KBNode.displayName(forPath:)), paths: net.weaklyLinked)
        }
    }

    /// A health row whose items each open a note. `labels[i]` is shown, `paths[i]`
    /// is opened. Shows count + top-N chips + a "show all" disclosure; "✓ none"
    /// when empty.
    @ViewBuilder
    private func pathRow(_ title: String, icon: String, labels: [String], paths: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: icon).font(.system(size: 12)).foregroundStyle(labels.isEmpty ? DS.Status.ok : DS.Status.warn)
                Text(labels.isEmpty ? "\(title): ✓ none" : "\(labels.count) \(title.lowercased())")
                    .font(DS.sans(12, weight: .medium)).foregroundStyle(DS.Ink.p2)
            }
            if !labels.isEmpty {
                chips(Array(labels.prefix(topN)), Array(paths.prefix(topN)))
                if labels.count > topN {
                    DisclosureGroup("Show all \(labels.count)") {
                        chips(Array(labels.dropFirst(topN)), Array(paths.dropFirst(topN)))
                            .padding(.top, 4)
                    }
                    .font(DS.sans(11)).foregroundStyle(DS.Accent.ink)
                }
            }
        }
    }

    private func chips(_ labels: [String], _ paths: [String]) -> some View {
        FlowLayout(spacing: 6) {
            ForEach(Array(zip(labels, paths).enumerated()), id: \.offset) { _, pair in
                Button { onOpen(pair.1) } label: {
                    Text(pair.0).font(DS.sans(11)).foregroundStyle(DS.Ink.p1)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Capsule().fill(DS.Paper.sunk))
                }.buttonStyle(.plain)
            }
        }
    }

    @ViewBuilder
    private func islandsRow(_ islands: [[String]]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "square.on.square.dashed").font(.system(size: 12))
                    .foregroundStyle(islands.isEmpty ? DS.Status.ok : DS.Status.warn)
                Text(islands.isEmpty ? "Disconnected islands: ✓ none" : "\(islands.count) disconnected islands")
                    .font(DS.sans(12, weight: .medium)).foregroundStyle(DS.Ink.p2)
            }
            ForEach(Array(islands.prefix(topN).enumerated()), id: \.offset) { _, island in
                chips(island.map(KBNode.displayName(forPath:)), island)
            }
            if islands.count > topN {
                DisclosureGroup("Show all \(islands.count)") {
                    ForEach(Array(islands.dropFirst(topN).enumerated()), id: \.offset) { _, island in
                        chips(island.map(KBNode.displayName(forPath:)), island)
                    }
                }.font(DS.sans(11)).foregroundStyle(DS.Accent.ink)
            }
        }
    }
}
```

Note: if `DS.Status.ok` doesn't exist, use `DS.Ink.p4` for the "none" state (check `DS.Status` — `warn` is used in `KBEditorView`; pick an existing calm color for ok).

- [ ] **Step 2: Embed in `KBOverviewView`**

In `KBOverviewView.swift` (post-feature-2 form), replace the header's `Text("\(stats.notes) notes · \(stats.links) connections")` line with `KBStatsView(service: service, onOpen: onNavigate)` placed as its own section between the title header and the QUICK ACCESS grid. Remove the now-unused local `stats` binding if nothing else uses it (the `present`/`links` computations stay).

- [ ] **Step 3: Build**

Run: `xcodebuild -scheme Scout -configuration Debug -destination 'platform=macOS' build 2>&1 | tail -15`
Expected: `** BUILD SUCCEEDED **` (if a `DS` token is missing, swap for the nearest existing one per the note above and rebuild).

- [ ] **Step 4: Run the full test target**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' 2>&1 | tail -15`
Expected: PASS — "Executed N tests, with 0 failures".

- [ ] **Step 5: Manual verification**

Via `/run`, on the KB overview:
1. A VAULT HEALTH block shows counts for dangling links / orphans / islands / weakly-linked.
2. Each non-empty row shows up to 5 clickable chips; clicking one opens that note.
3. Rows with > 5 items show "Show all N" that expands the rest.
4. A metric with zero problems shows "✓ none".

- [ ] **Step 6: Commit**

```bash
git add Scout/KnowledgeBase/Views/KBStatsView.swift Scout/KnowledgeBase/Views/KBOverviewView.swift
git commit -m "feat(kb): vault-health stats block on the overview (orphans/dangling/islands/weak)"
```

---

## Task 3: `KBStatsView` — Insight block

**Files:**
- Modify: `Scout/KnowledgeBase/Views/KBStatsView.swift`

**Interfaces:**
- Consumes: `KBNetworkStats.topHubs/avgDegree/maxDegree/byType/clusterCount/largestComponentSize`; `KBEntityGroup.color/label`.
- Produces: `insightBlock` in `KBStatsView`.

**No unit test**; build + manual `/run`.

- [ ] **Step 1: Add `insightBlock` and call it from `body`**

In `KBStatsView.body`, uncomment/insert `insightBlock(net)` after `healthBlock(net)`. Add:

```swift
    // MARK: - Insight

    @ViewBuilder
    private func insightBlock(_ net: KBNetworkStats) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("INSIGHT").font(DS.sans(10, weight: .semibold)).tracking(0.6).foregroundStyle(DS.Ink.p4)

            Text("avg \(String(format: "%.1f", net.avgDegree)) · max \(net.maxDegree) connections per note")
                .font(DS.sans(12)).foregroundStyle(DS.Ink.p2)

            Text(net.clusterCount <= 1
                 ? "1 connected cluster · largest covers \(net.largestComponentSize) notes"
                 : "\(net.clusterCount) clusters · largest covers \(net.largestComponentSize) notes")
                .font(DS.sans(12)).foregroundStyle(DS.Ink.p2)

            if !net.topHubs.isEmpty {
                Text("Top hubs").font(DS.sans(11, weight: .semibold)).foregroundStyle(DS.Ink.p3)
                FlowLayout(spacing: 6) {
                    ForEach(net.topHubs.prefix(5)) { hub in
                        Button { onOpen(hub.path) } label: {
                            HStack(spacing: 4) {
                                Text(KBNode.displayName(forPath: hub.path)).font(DS.sans(11)).foregroundStyle(DS.Ink.p1)
                                Text("\(hub.degree)").font(DS.mono(10)).foregroundStyle(DS.Ink.p4)
                            }
                            .padding(.horizontal, 8).padding(.vertical, 3)
                            .background(Capsule().fill(DS.Paper.sunk))
                        }.buttonStyle(.plain)
                    }
                }
                if net.topHubs.count > 5 {
                    DisclosureGroup("Show all \(net.topHubs.count)") {
                        FlowLayout(spacing: 6) {
                            ForEach(net.topHubs.dropFirst(5)) { hub in
                                Button { onOpen(hub.path) } label: {
                                    Text("\(KBNode.displayName(forPath: hub.path)) (\(hub.degree))")
                                        .font(DS.sans(11)).foregroundStyle(DS.Ink.p1)
                                        .padding(.horizontal, 8).padding(.vertical, 3)
                                        .background(Capsule().fill(DS.Paper.sunk))
                                }.buttonStyle(.plain)
                            }
                        }.padding(.top, 4)
                    }.font(DS.sans(11)).foregroundStyle(DS.Accent.ink)
                }
            }

            Text("By type").font(DS.sans(11, weight: .semibold)).foregroundStyle(DS.Ink.p3)
            FlowLayout(spacing: 10) {
                ForEach(net.byType.filter { $0.count > 0 }) { tc in
                    HStack(spacing: 4) {
                        Circle().fill(tc.group.color).frame(width: 7, height: 7)
                        Text("\(tc.group.label) \(tc.count)").font(DS.sans(11)).foregroundStyle(DS.Ink.p2)
                    }
                }
            }
        }
    }
```

- [ ] **Step 2: Build**

Run: `xcodebuild -scheme Scout -configuration Debug -destination 'platform=macOS' build 2>&1 | tail -15`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 3: Run the full test target**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' 2>&1 | tail -15`
Expected: PASS — "Executed N tests, with 0 failures".

- [ ] **Step 4: Manual verification**

Via `/run`, on the KB overview INSIGHT block:
1. Degree line reads "avg X.X · max N connections per note".
2. Clusters line reads "K clusters · largest covers M notes".
3. Top hubs shows the 5 most-connected notes with their degree; clicking opens one; "show all" expands to 20.
4. "By type" shows a colored count per present entity group, matching the map legend colors.

- [ ] **Step 5: Commit**

```bash
git add Scout/KnowledgeBase/Views/KBStatsView.swift
git commit -m "feat(kb): network insight block — degree summary, top hubs, per-type, components"
```

---

## Self-Review

**1. Spec coverage** (against `2026-07-07-kb-network-stats-design.md`):
- All 4 health metrics (orphans, weakly-linked, dangling, islands) → Task 1 (engine) + Task 2 (view). ✓
- All 4 insight metrics (top hubs, degree summary, per-type, components) → Task 1 + Task 3. ✓
- Count + top-5 + "show all" disclosure; "✓ none" empty state → Task 2/3. ✓
- Click-to-open on every note/hub/island item → `onOpen` throughout. ✓
- One-pass, no new I/O → `networkStats()` reads only index + edges. ✓
- Exact definitions (orphan/weak/dangling/island/hub) → encoded + tested in Task 1. ✓
- Stacks on feature 2 → Global Constraints + KBOverviewView embed note. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to"; every code step is complete. The only conditional is the `DS.Status.ok` fallback note — an explicit, resolvable instruction, not a placeholder.

**3. Type consistency:** `networkStats(hubCap:)`, `KBNetworkStats` fields, `KBDanglingLink`/`KBHub`/`KBTypeCount`, `KBStatsView(service:onOpen:)` — names identical across tasks. `KBEntityGroup.color/label/allCases` and `KBNode.displayName(forPath:)` used as defined.

## Notes for the implementer

- **Base on feature 2.** Branch from feature-2's code so `KBOverviewView` already has `KBMapView` and the degree helpers exist. If feature 2 changed `KBOverviewView`'s structure, place `KBStatsView` between the title header and QUICK ACCESS; the exact lines depend on the merged feature-2 form.
- **`DS` tokens:** verify `DS.Status.ok`/`DS.Status.warn` exist (`warn` is used in `KBEditorView`); if `ok` is absent, use a calm existing color (e.g. `DS.Ink.p4`). Don't invent tokens.
- **Recompute cost:** `networkStats()` runs per overview `body` eval like `graphStats()` already does; O(nodes+edges). Memoize by index identity only if profiling shows churn.
- **Overlap with feature 2:** `topHubs` here and feature 2's `KBGraph.topHubs` both rank by degree; they operate on different inputs (paths vs `KBGraphNode`s) so no shared type is forced, but keep the degree-desc/id-asc ordering identical for consistency.
```

