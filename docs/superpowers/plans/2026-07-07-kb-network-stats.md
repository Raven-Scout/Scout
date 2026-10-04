# KB Network-Analysis Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the overview's single "N notes · M connections" line into a network section with a **Vault Health** block (dangling links, orphans, disconnected islands, weakly-linked — clickable) and an **Insight** block (degree summary, components, top hubs, per-type breakdown), all from one in-memory pass.

**Architecture:** A new `networkStats() -> KBNetworkStats` on `KnowledgeBaseService` computes every metric in a single traversal of the existing index + `undirectedEdges()` adjacency (no new I/O). Adjacency comes from one helper that `localGraph(around:)` shares. Display strings are pure, tested helpers on `KBNetworkStats`. A new `KBStatsView` renders a `KBNetworkStats` value, and `KBOverviewView` computes it once and embeds the view.

**Tech Stack:** Swift, SwiftUI; Swift Testing. Synchronized file groups (new files in `Scout/` and `ScoutTests/` auto-compile — no `.pbxproj` edits).

> **Revalidated 2026-10-03** against `main` @ `7a037c9`. Feature 2 (graph navigability) shipped in #90, so the old "stack on feature 2" caveat is gone and this branches from `main`. Every file/line/symbol below was re-checked against that commit. The spec's "Revalidation 2026-10-03" section lists what moved.

## Global Constraints

- **No new data source / disk I/O:** compute only from `index` (`outByFile`, `stemToPath`, `typeByFile`), `tree.flatMap(\.allFiles)`, and `undirectedEdges()`. *One approved exception (amendment 2026-10-04, Task 7): `index.vaultNames`, a filename-only listing of `~/Scout` taken during the existing off-main reparse.*
- **Definitions (exact):** orphan = degree 0; weakly-linked = degree exactly 1; dangling = an outgoing `[[target]]` with no `stemToPath[target.lowercased()]` resolution; island = a connected component of size ≥ 2 that is **not** the largest component; hub = a note ranked by degree (degree > 0), degree desc / path asc.
- **Determinism:** every output list is sorted. Problem lists are sorted by path; hubs by degree then path. Components are ranked by size desc, then by their first (sorted) path asc, so the "mainland" among equal-size components is stable across reparses.
- **Compact by default:** each list shows count + top 5, with a "Show all N" disclosure. Empty metric → "✓ none".
- **Isolation:** the app target uses `SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor`. Model structs are `nonisolated` like their neighbours in `KBGraph.swift`; service-backed test suites are `@MainActor` like `KBServiceGraphTests`.
- **Public repo — anonymized literals only** (repo `CLAUDE.md`). Every fixture name below (`hub`, `alex`, `priya`, `sam`, `ghost`, `island-a/b`, `lonely`, `duo-a…d`) was checked against `~/Scout` (excluding `~/Scout/.claude/`) at **0** hits on 2026-10-03. Re-check any literal you add.
- **Buttons:** chip buttons use `.buttonStyle(.plainHit)` (whole padded frame clickable, issue #16), not `.plain`.
- **Test command:** `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS'`. An `-only-testing:ScoutTests/<StructName>` selector is fine for speed only when it targets a real `@Suite` **type name** and the output shows a non-zero test count. Otherwise it silently runs zero tests. The final verdict is always the whole `ScoutTests` target.
- **Coverage floor:** CI fails below `scripts/coverage-floor.txt` (70.0%) via `scripts/check-coverage.sh`. New logic gets unit tests; only SwiftUI layout stays untested.
- **Platform:** macOS 13+; destination `platform=macOS`.

---

## File Structure

- **Modify** `Scout/KnowledgeBase/KnowledgeBaseService.swift` — extract `adjacency(of:)`, use it in `localGraph(around:)`, add `networkStats(hubCap:)`.
- **Modify** `Scout/KnowledgeBase/Models/KBGraph.swift` — add `KBNetworkStats` + `KBDanglingLink`, `KBHub`, `KBTypeCount`, and the display-string extension.
- **Create** `Scout/KnowledgeBase/Views/KBStatsView.swift` — health + insight blocks.
- **Modify** `Scout/KnowledgeBase/Views/KBOverviewView.swift` — compute `networkStats()` once, feed the header + `KBStatsView`.
- **Create** `ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift` — engine + display-string suites.
- **Create** `ScoutTests/KnowledgeBase/KBLinkResolutionTests.swift` — resolver + dangling-exclusion suites (Tasks 6–7, amendment 2026-10-04).

---

## Task 1: Extract the shared adjacency helper (refactor, no behavior change)

**Files:**
- Modify: `Scout/KnowledgeBase/KnowledgeBaseService.swift` (`localGraph(around:depth:maxNodes:)`, lines 285-326)

The spec promised a single degree/adjacency source. `localGraph` builds `[String: Set<String>]` inline (lines 287-292). Lift that into a helper so `networkStats()` (Task 2) uses the same one.

- [ ] **Step 1: Confirm the guard tests are green before touching anything**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBServiceGraphTests -only-testing:ScoutTests/KnowledgeBaseServiceHubGraphTests 2>&1 | tail -20`
Expected: PASS with a non-zero test count (`KBServiceGraphTests.resolvesLinksBacklinksAndLocalGraph` exercises `localGraph`).

- [ ] **Step 2: Add the helper and switch `localGraph` to it**

In `KnowledgeBaseService.swift`, directly after `undirectedEdges()` (ends line 281), add:

```swift
    /// Undirected adjacency over `edges`. Shared by `localGraph(around:)` and
    /// `networkStats()` so "degree" means the same thing in both.
    nonisolated static func adjacency(of edges: Set<KBGraphEdge>) -> [String: Set<String>] {
        var adj: [String: Set<String>] = [:]
        for e in edges {
            adj[e.from, default: []].insert(e.to)
            adj[e.to, default: []].insert(e.from)
        }
        return adj
    }
```

In `localGraph(around:depth:maxNodes:)`, replace:

```swift
        // Adjacency.
        var adj: [String: Set<String>] = [:]
        for e in edgeSet {
            adj[e.from, default: []].insert(e.to)
            adj[e.to, default: []].insert(e.from)
        }
```

with:

```swift
        let adj = Self.adjacency(of: edgeSet)
```

- [ ] **Step 3: Re-run the guard tests**

Same command as Step 1. Expected: PASS, same test count.

- [ ] **Step 4: Commit**

```bash
git add Scout/KnowledgeBase/KnowledgeBaseService.swift
git commit -m "refactor(kb): share one adjacency helper across graph builders"
```

---

## Task 2: `KBNetworkStats` model + `networkStats()` engine

**Files:**
- Modify: `Scout/KnowledgeBase/Models/KBGraph.swift` (append after `KBIndex`, which ends at line 148 — end of file)
- Modify: `Scout/KnowledgeBase/KnowledgeBaseService.swift` (after `hubGraph(maxNodes:)`, which ends at line 353)
- Test: `ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift`

**Interfaces:**
- Consumes: `undirectedEdges()`, `adjacency(of:)` (Task 1), `index.outByFile`/`stemToPath`/`typeByFile`, `tree.flatMap(\.allFiles)`, `KBEntityGroup.of(_:type:)` / `.allCases`.
- Produces:
  - `KBNetworkStats` with fields `noteCount:Int`, `linkCount:Int`, `orphans:[String]`, `weaklyLinked:[String]`, `dangling:[KBDanglingLink]`, `islands:[[String]]`, `topHubs:[KBHub]`, `avgDegree:Double`, `maxDegree:Int`, `byType:[KBTypeCount]`, `clusterCount:Int`, `largestComponentSize:Int`; and `.empty`.
  - `KBDanglingLink(source:target:)`, `KBHub(path:degree:)`, `KBTypeCount(group:count:)`, all `Identifiable, Equatable`.
  - `KnowledgeBaseService.networkStats(hubCap: Int = 20) -> KBNetworkStats`.

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("KnowledgeBaseService networkStats")
struct KBNetworkStatsTests {
    /// Write `files` (name → body) into a fresh `<tmp>/knowledge-base/`.
    private func makeKB(_ files: [String: String]) throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbstats-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        for (name, body) in files {
            try body.write(to: kb.appendingPathComponent(name), atomically: true, encoding: .utf8)
        }
        return root
    }

    /// A 4-note mainland (hub + 3 leaves, one linking back as `[[Hub]]`), a
    /// 2-note island, one orphan, and one dangling link (sam → ghost).
    private static let mainland: [String: String] = [
        "hub.md": "[[alex]] [[priya]] [[sam]]",
        "alex.md": "[[Hub]]",                  // case variant — resolves, not dangling
        "priya.md": "[[hub]]",
        "sam.md": "[[hub]] [[ghost]]",         // ghost has no note → dangling
        "island-a.md": "[[island-b]]",
        "island-b.md": "[[island-a]]",
        "lonely.md": "no links here",          // orphan
    ]

    private func load(_ files: [String: String]) async throws -> (KnowledgeBaseService, URL) {
        let root = try makeKB(files)
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        return (svc, root)
    }

    private func stats(_ files: [String: String], hubCap: Int = 20) async throws -> KBNetworkStats {
        let (svc, root) = try await load(files)
        defer { try? FileManager.default.removeItem(at: root) }
        return svc.networkStats(hubCap: hubCap)
    }

    @Test func orphansAndWeaklyLinked() async throws {
        let s = try await stats(Self.mainland)
        #expect(s.orphans == ["knowledge-base/lonely.md"])
        #expect(s.weaklyLinked == [
            "knowledge-base/alex.md", "knowledge-base/island-a.md", "knowledge-base/island-b.md",
            "knowledge-base/priya.md", "knowledge-base/sam.md",
        ])
    }

    @Test func danglingLinkDetectedAndCaseVariantResolves() async throws {
        let s = try await stats(Self.mainland)
        #expect(s.dangling == [KBDanglingLink(source: "knowledge-base/sam.md", target: "ghost")])
    }

    @Test func islandsExcludeMainlandAndOrphan() async throws {
        let s = try await stats(Self.mainland)
        #expect(s.islands == [["knowledge-base/island-a.md", "knowledge-base/island-b.md"]])
        #expect(s.largestComponentSize == 4)                       // hub + 3 leaves
        #expect(s.clusterCount == 2)                               // mainland + island
    }

    @Test func equalSizeComponentsPickTheMainlandDeterministically() async throws {
        let s = try await stats([
            "duo-a.md": "[[duo-b]]", "duo-b.md": "",
            "duo-c.md": "[[duo-d]]", "duo-d.md": "",
        ])
        #expect(s.largestComponentSize == 2)
        #expect(s.clusterCount == 2)
        #expect(s.islands == [["knowledge-base/duo-c.md", "knowledge-base/duo-d.md"]])
    }

    @Test func hubsOrderedByDegreeThenPathAndCapped() async throws {
        let s = try await stats(Self.mainland)
        #expect(s.topHubs.map(\.path) == [
            "knowledge-base/hub.md", "knowledge-base/alex.md", "knowledge-base/island-a.md",
            "knowledge-base/island-b.md", "knowledge-base/priya.md", "knowledge-base/sam.md",
        ])                                                         // lonely (degree 0) excluded
        #expect(s.topHubs.first?.degree == 3)
        let capped = try await stats(Self.mainland, hubCap: 2)
        #expect(capped.topHubs.map(\.path) == ["knowledge-base/hub.md", "knowledge-base/alex.md"])
    }

    @Test func degreeSummaryAndTotals() async throws {
        let (svc, root) = try await load(Self.mainland)
        defer { try? FileManager.default.removeItem(at: root) }
        let s = svc.networkStats()
        #expect(s.maxDegree == 3)
        #expect(s.avgDegree == 8.0 / 7.0)                          // 2·4 edges / 7 notes
        #expect(s.noteCount == 7)
        #expect(s.linkCount == 4)
        let legacy = svc.graphStats()
        #expect(s.noteCount == legacy.notes)
        #expect(s.linkCount == legacy.links)
    }

    @Test func byTypeCountsEveryNoteInEveryGroup() async throws {
        let s = try await stats(Self.mainland)
        #expect(s.byType.map(\.group) == KBEntityGroup.allCases)   // all groups, 0s included
        #expect(s.byType.reduce(0) { $0 + $1.count } == 7)
    }

    @Test func emptyVaultIsEmptyStats() async throws {
        let s = try await stats([:])
        #expect(s == .empty)
        #expect(s.byType.count == KBEntityGroup.allCases.count)
        #expect(s.byType.allSatisfy { $0.count == 0 })
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBNetworkStatsTests 2>&1 | tail -20`
Expected: compile failure — `networkStats` / `KBNetworkStats` / `KBDanglingLink` don't exist.

- [ ] **Step 3: Add the model types**

Append to `Scout/KnowledgeBase/Models/KBGraph.swift`, after `KBIndex`:

```swift

// MARK: - Network stats

/// An outgoing `[[target]]` that resolves to no note.
nonisolated struct KBDanglingLink: Identifiable, Equatable {
    let source: String     // note holding the broken link (relative path)
    let target: String     // unresolved [[target]] text, original case
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

/// Whole-KB network analysis for the overview: totals, actionable health
/// signals, and read-only connectivity insight. Computed in one pass over the
/// index + edges by `KnowledgeBaseService.networkStats()`.
nonisolated struct KBNetworkStats: Equatable {
    let noteCount: Int
    let linkCount: Int
    let orphans: [String]                // degree 0, path asc
    let weaklyLinked: [String]           // degree exactly 1, path asc
    let dangling: [KBDanglingLink]       // source asc, then target asc
    let islands: [[String]]              // components of size >= 2 except the largest
    let topHubs: [KBHub]                 // degree desc, path asc; degree > 0; capped
    let avgDegree: Double
    let maxDegree: Int
    let byType: [KBTypeCount]            // every KBEntityGroup, in allCases order, 0s included
    let clusterCount: Int                // components with size >= 2
    let largestComponentSize: Int

    static let empty = KBNetworkStats(
        noteCount: 0, linkCount: 0, orphans: [], weaklyLinked: [], dangling: [], islands: [],
        topHubs: [], avgDegree: 0, maxDegree: 0,
        byType: KBEntityGroup.allCases.map { KBTypeCount(group: $0, count: 0) },
        clusterCount: 0, largestComponentSize: 0)
}
```

- [ ] **Step 4: Implement `networkStats()`**

In `Scout/KnowledgeBase/KnowledgeBaseService.swift`, after `hubGraph(maxNodes:)`, add:

```swift

    /// One-pass network analysis for the overview: totals, orphans /
    /// weakly-linked / dangling / islands (health) and hubs / degree /
    /// per-type / components (insight). Reads only the in-memory index + edges.
    func networkStats(hubCap: Int = 20) -> KBNetworkStats {
        let notes = tree.flatMap(\.allFiles).filter { $0.ext == "md" }.map(\.relativePath)
        guard !notes.isEmpty else { return .empty }
        let edgeSet = undirectedEdges()
        let adj = Self.adjacency(of: edgeSet)
        let degree: (String) -> Int = { adj[$0]?.count ?? 0 }

        // Health: orphans / weakly-linked.
        let orphans = notes.filter { degree($0) == 0 }.sorted()
        let weaklyLinked = notes.filter { degree($0) == 1 }.sorted()

        // Health: dangling links. `extractWikilinks` already de-dups targets
        // per note, so each (source, target) appears once.
        var dangling: [KBDanglingLink] = []
        for (source, targets) in index.outByFile {
            for t in targets where index.stemToPath[t.lowercased()] == nil {
                dangling.append(KBDanglingLink(source: source, target: t))
            }
        }
        dangling.sort { $0.source != $1.source ? $0.source < $1.source : $0.target < $1.target }

        // Connected components (iterative DFS). Each is sorted, then ranked by
        // size desc and first path asc so the mainland is stable on ties.
        var seen = Set<String>()
        var components: [[String]] = []
        for start in notes where !seen.contains(start) {
            var comp: [String] = []
            var stack = [start]
            seen.insert(start)
            while let node = stack.popLast() {
                comp.append(node)
                for nb in adj[node] ?? [] where !seen.contains(nb) {
                    seen.insert(nb)
                    stack.append(nb)
                }
            }
            components.append(comp.sorted())
        }
        components.sort { $0.count != $1.count ? $0.count > $1.count : $0[0] < $1[0] }
        let clusters = components.filter { $0.count >= 2 }

        // Insight: degree summary, hubs, per-type.
        let topHubs = notes
            .filter { degree($0) > 0 }
            .sorted { degree($0) != degree($1) ? degree($0) > degree($1) : $0 < $1 }
            .prefix(hubCap)
            .map { KBHub(path: $0, degree: degree($0)) }
        var counts: [KBEntityGroup: Int] = [:]
        for n in notes { counts[KBEntityGroup.of(n, type: index.typeByFile[n]), default: 0] += 1 }

        return KBNetworkStats(
            noteCount: notes.count,
            linkCount: edgeSet.count,
            orphans: orphans,
            weaklyLinked: weaklyLinked,
            dangling: dangling,
            islands: Array(clusters.dropFirst()),
            topHubs: Array(topHubs),
            avgDegree: Double(2 * edgeSet.count) / Double(notes.count),
            maxDegree: notes.map(degree).max() ?? 0,
            byType: KBEntityGroup.allCases.map { KBTypeCount(group: $0, count: counts[$0] ?? 0) },
            clusterCount: clusters.count,
            largestComponentSize: components.first?.count ?? 0)
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBNetworkStatsTests 2>&1 | tail -20`
Expected: PASS — 8 tests, 0 failures (confirm the count is 8, not 0).

- [ ] **Step 6: Commit**

```bash
git add Scout/KnowledgeBase/Models/KBGraph.swift Scout/KnowledgeBase/KnowledgeBaseService.swift ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift
git commit -m "feat(kb): networkStats() — orphans/dangling/islands + hubs/degree/type/components"
```

---

## Task 3: Display-string helpers (tested)

**Files:**
- Modify: `Scout/KnowledgeBase/Models/KBGraph.swift` (after `KBNetworkStats`)
- Test: `ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift` (new suite in the same file)

**Interfaces:**
- Produces: `KBNetworkStats.degreeSummary: String`, `KBNetworkStats.componentsSummary: String`, `static KBNetworkStats.healthTitle(_ count: Int, singular: String, plural: String) -> String`.

- [ ] **Step 1: Write the failing test**

Append to `ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift`:

```swift

@Suite("KBNetworkStats display strings")
struct KBNetworkStatsDisplayTests {
    private func net(avg: Double = 0, max: Int = 0, clusters: Int = 0, largest: Int = 0) -> KBNetworkStats {
        KBNetworkStats(noteCount: 0, linkCount: 0, orphans: [], weaklyLinked: [], dangling: [],
                       islands: [], topHubs: [], avgDegree: avg, maxDegree: max, byType: [],
                       clusterCount: clusters, largestComponentSize: largest)
    }

    @Test func degreeSummary() {
        #expect(net(avg: 8.0 / 7.0, max: 3).degreeSummary == "avg 1.1 · max 3 connections per note")
        #expect(KBNetworkStats.empty.degreeSummary == "avg 0.0 · max 0 connections per note")
    }

    @Test func componentsSummary() {
        #expect(net().componentsSummary == "No linked notes yet")
        #expect(net(clusters: 1, largest: 4).componentsSummary == "1 main cluster · covers 4 notes")
        #expect(net(clusters: 2, largest: 4).componentsSummary
                == "1 main cluster + 1 island · largest covers 4 notes")
        #expect(net(clusters: 4, largest: 10).componentsSummary
                == "1 main cluster + 3 islands · largest covers 10 notes")
    }

    @Test func healthTitle() {
        #expect(KBNetworkStats.healthTitle(0, singular: "orphaned note", plural: "orphaned notes")
                == "Orphaned notes: ✓ none")
        #expect(KBNetworkStats.healthTitle(1, singular: "orphaned note", plural: "orphaned notes")
                == "1 orphaned note")
        #expect(KBNetworkStats.healthTitle(5, singular: "orphaned note", plural: "orphaned notes")
                == "5 orphaned notes")
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBNetworkStatsDisplayTests 2>&1 | tail -20`
Expected: compile failure — `degreeSummary` / `componentsSummary` / `healthTitle` don't exist.

- [ ] **Step 3: Implement**

Append to `Scout/KnowledgeBase/Models/KBGraph.swift`, after `KBNetworkStats`:

```swift

extension KBNetworkStats {
    /// "avg 1.1 · max 3 connections per note".
    var degreeSummary: String {
        "avg \(String(format: "%.1f", avgDegree)) · max \(maxDegree) connections per note"
    }

    /// "1 main cluster + K islands · largest covers M notes", with the
    /// one-cluster and no-links cases worded on their own.
    var componentsSummary: String {
        switch clusterCount {
        case 0: return "No linked notes yet"
        case 1: return "1 main cluster · covers \(largestComponentSize) notes"
        default:
            let islands = clusterCount - 1
            return "1 main cluster + \(islands) island\(islands == 1 ? "" : "s")"
                + " · largest covers \(largestComponentSize) notes"
        }
    }

    /// A health row's title: "Orphaned notes: ✓ none", "1 orphaned note",
    /// "5 orphaned notes".
    static func healthTitle(_ count: Int, singular: String, plural: String) -> String {
        switch count {
        case 0: return plural.prefix(1).uppercased() + plural.dropFirst() + ": ✓ none"
        case 1: return "1 \(singular)"
        default: return "\(count) \(plural)"
        }
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Same command as Step 2. Expected: PASS — 3 tests, 0 failures.

- [ ] **Step 5: Commit**

```bash
git add Scout/KnowledgeBase/Models/KBGraph.swift ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift
git commit -m "feat(kb): tested display strings for network stats"
```

---

## Task 4: `KBStatsView` — Vault Health block + embed in overview

**Files:**
- Create: `Scout/KnowledgeBase/Views/KBStatsView.swift`
- Modify: `Scout/KnowledgeBase/Views/KBOverviewView.swift`

**Interfaces:**
- Consumes: `KBNetworkStats` + its display helpers (Tasks 2–3), `KBNode.displayName(forPath:)`, `FlowLayout`, `DS.Status.ok/warn`, `DS.Accent.ink`, `DS.Paper.sunk`, `.plainHit` (all exist on `main`).
- Produces: `KBStatsView(stats:onOpen:)`. `onOpen(String)` opens a note in the editor.

**No unit test** (logic covered by Tasks 2–3); build + manual `/run`.

- [ ] **Step 1: Create `KBStatsView` with the health block**

Create `Scout/KnowledgeBase/Views/KBStatsView.swift`:

```swift
import SwiftUI

/// The overview's network section: vault-health problem lists (clickable) plus
/// read-only connectivity insight, rendered from one `KBNetworkStats` value.
struct KBStatsView: View {
    let stats: KBNetworkStats
    /// Open a note in the editor.
    let onOpen: (String) -> Void

    private let topN = 5

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            healthBlock
            // insightBlock added in Task 5
        }
    }

    // MARK: - Health

    private var healthBlock: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionLabel("VAULT HEALTH")
            row(KBNetworkStats.healthTitle(stats.dangling.count,
                                           singular: "dangling link", plural: "dangling links"),
                icon: "link.badge.plus",
                labels: stats.dangling.map { "\(KBNode.displayName(forPath: $0.source)) → \($0.target)" },
                paths: stats.dangling.map(\.source))
            row(KBNetworkStats.healthTitle(stats.orphans.count,
                                           singular: "orphaned note", plural: "orphaned notes"),
                icon: "circle.dashed",
                labels: stats.orphans.map(KBNode.displayName(forPath:)), paths: stats.orphans)
            islandsRow
            row(KBNetworkStats.healthTitle(stats.weaklyLinked.count,
                                           singular: "weakly linked note", plural: "weakly linked notes"),
                icon: "link",
                labels: stats.weaklyLinked.map(KBNode.displayName(forPath:)), paths: stats.weaklyLinked)
        }
    }

    /// A health row: an ok/warn icon + title, then the items as chips.
    private func row(_ title: String, icon: String, labels: [String], paths: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            rowHeader(title, icon: icon, clean: labels.isEmpty)
            if !labels.isEmpty { chipList(labels, paths) }
        }
    }

    /// Islands are lists of lists: one chip row per island, top-N islands
    /// shown, the rest behind a disclosure.
    private var islandsRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            rowHeader(KBNetworkStats.healthTitle(stats.islands.count,
                                                 singular: "disconnected island",
                                                 plural: "disconnected islands"),
                      icon: "square.on.square.dashed", clean: stats.islands.isEmpty)
            ForEach(Array(stats.islands.prefix(topN).enumerated()), id: \.offset) { _, island in
                chips(island.map(KBNode.displayName(forPath:)), island)
            }
            if stats.islands.count > topN {
                DisclosureGroup("Show all \(stats.islands.count)") {
                    ForEach(Array(stats.islands.dropFirst(topN).enumerated()), id: \.offset) { _, island in
                        chips(island.map(KBNode.displayName(forPath:)), island)
                    }
                    .padding(.top, 4)
                }
                .font(DS.sans(11)).foregroundStyle(DS.Accent.ink)
            }
        }
    }

    // MARK: - Building blocks

    private func sectionLabel(_ text: String) -> some View {
        Text(text).font(DS.sans(10, weight: .semibold)).tracking(0.6).foregroundStyle(DS.Ink.p4)
    }

    private func rowHeader(_ title: String, icon: String, clean: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 12))
                .foregroundStyle(clean ? DS.Status.ok : DS.Status.warn)
            Text(title).font(DS.sans(12, weight: .medium)).foregroundStyle(DS.Ink.p2)
        }
    }

    /// The first `topN` items as chips, plus a "Show all N" disclosure for the
    /// rest. `labels[i]` is shown; `paths[i]` is opened.
    @ViewBuilder
    private func chipList(_ labels: [String], _ paths: [String]) -> some View {
        chips(Array(labels.prefix(topN)), Array(paths.prefix(topN)))
        if labels.count > topN {
            DisclosureGroup("Show all \(labels.count)") {
                chips(Array(labels.dropFirst(topN)), Array(paths.dropFirst(topN)))
                    .padding(.top, 4)
            }
            .font(DS.sans(11)).foregroundStyle(DS.Accent.ink)
        }
    }

    private func chips(_ labels: [String], _ paths: [String]) -> some View {
        FlowLayout(spacing: 6) {
            ForEach(Array(zip(labels, paths).enumerated()), id: \.offset) { _, pair in
                Button { onOpen(pair.1) } label: {
                    Text(pair.0).font(DS.sans(11)).foregroundStyle(DS.Ink.p1)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Capsule().fill(DS.Paper.sunk))
                }
                .buttonStyle(.plainHit)
            }
        }
    }
}
```

- [ ] **Step 2: Embed in `KBOverviewView`**

In `Scout/KnowledgeBase/Views/KBOverviewView.swift`:

1. Line 25 — replace `let stats = service.graphStats()` with `let net = service.networkStats()`. (`stats` has no other use in the file; `present`/`links` stay.)
2. Line 41 — replace `Text("\(stats.notes) notes · \(stats.links) connections")` with `Text("\(net.noteCount) notes · \(net.linkCount) connections")`.
3. After the header `VStack` closes (line 43), before `if !links.isEmpty {` (line 45), insert:

```swift

                KBStatsView(stats: net, onOpen: onNavigate)
```

The resulting order is: title + totals → network section → QUICK ACCESS → MAP (`KBMapView`, line 71) → hint text.

- [ ] **Step 3: Build**

Run: `xcodebuild -project Scout.xcodeproj -scheme Scout -configuration Debug -destination 'platform=macOS' build 2>&1 | tail -15`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 4: Run the full test target**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests 2>&1 | tail -15`
Expected: `** TEST SUCCEEDED **`, 0 failures. (Known load-flaky tests: `watcherCoalescesAppendBursts`, `FileWatcherTests`' 3 s ceiling. If one of those fails, re-run it alone before treating it as a regression.)

- [ ] **Step 5: Manual verification**

Via `/run`, on the KB overview:
1. A VAULT HEALTH block sits between the title and QUICK ACCESS, showing dangling links / orphans / islands / weakly-linked.
2. Each non-empty row shows up to 5 clickable chips; clicking one opens that note (the whole chip is clickable, not just the glyphs).
3. Rows with > 5 items show "Show all N", which expands the rest.
4. A metric with zero problems shows "<Metric>: ✓ none" with a green icon.
5. The header totals match what they showed before the change.

- [ ] **Step 6: Commit**

```bash
git add Scout/KnowledgeBase/Views/KBStatsView.swift Scout/KnowledgeBase/Views/KBOverviewView.swift
git commit -m "feat(kb): vault-health stats block on the overview (dangling/orphans/islands/weak)"
```

---

## Task 5: `KBStatsView` — Insight block

**Files:**
- Modify: `Scout/KnowledgeBase/Views/KBStatsView.swift`

**Interfaces:**
- Consumes: `KBNetworkStats.degreeSummary/componentsSummary/topHubs/byType`; `KBEntityGroup.color/label` (the same colors `KBGraphLegend` in `KBLocalGraphView.swift:77` draws).
- Produces: `insightBlock` in `KBStatsView`.

**No unit test** (strings covered in Task 3); build + manual `/run`.

- [ ] **Step 1: Add `insightBlock` and call it from `body`**

In `KBStatsView.body`, replace the `// insightBlock added in Task 5` comment with `insightBlock`. Add after the health section:

```swift
    // MARK: - Insight

    private var insightBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionLabel("INSIGHT")
            Text(stats.degreeSummary).font(DS.sans(12)).foregroundStyle(DS.Ink.p2)
            Text(stats.componentsSummary).font(DS.sans(12)).foregroundStyle(DS.Ink.p2)

            if !stats.topHubs.isEmpty {
                Text("Top hubs").font(DS.sans(11, weight: .semibold)).foregroundStyle(DS.Ink.p3)
                chipList(stats.topHubs.map { "\(KBNode.displayName(forPath: $0.path)) · \($0.degree)" },
                         stats.topHubs.map(\.path))
            }

            Text("By type").font(DS.sans(11, weight: .semibold)).foregroundStyle(DS.Ink.p3)
            FlowLayout(spacing: 10) {
                ForEach(stats.byType.filter { $0.count > 0 }) { tc in
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

Run: `xcodebuild -project Scout.xcodeproj -scheme Scout -configuration Debug -destination 'platform=macOS' build 2>&1 | tail -15`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 3: Manual verification**

Via `/run`, on the KB overview INSIGHT block:
1. Degree line reads "avg X.X · max N connections per note".
2. Components line reads "1 main cluster + K islands · largest covers M notes" (or the 1-cluster / no-links wording).
3. Top hubs shows the 5 most-connected notes as "name · degree"; clicking opens one; "Show all" expands to 20.
4. "By type" shows a colored count per present entity group, matching the map legend colors.

- [ ] **Step 4: Commit**

```bash
git add Scout/KnowledgeBase/Views/KBStatsView.swift
git commit -m "feat(kb): network insight block — degree summary, components, top hubs, per-type"
```

---

> **Amendment 2026-10-04.** Tasks 1–5 shipped as planned. Running them against the real vault returned 6,289 "dangling" links, most of them not broken (see the spec's "Amendment 2026-10-04"). Jordan chose: fix the resolver everywhere, and count only truly missing links as dangling. Tasks 6–8 implement that plus memoization; verification moves to Task 9.

## Task 6: One shared wikilink resolver (path, anchor and `.md` forms)

**Files:**
- Modify: `Scout/KnowledgeBase/Models/KBGraph.swift` (`KBIndex` extension)
- Modify: `Scout/KnowledgeBase/KnowledgeBaseService.swift` (`resolveWikilink`, `outgoingLinks`, `backlinks` + `excerpt`, `undirectedEdges`, `computeNetworkStats` dangling check)
- Test: `ScoutTests/KnowledgeBase/KBLinkResolutionTests.swift`

**Interfaces:**
- Produces: `static KBIndex.linkKey(_:) -> String`, `KBIndex.resolve(_ target: String, from source: String? = nil) -> String?`.
- Every former `index.stemToPath[x.lowercased()]` lookup in the service goes through `index.resolve`.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/KnowledgeBase/KBLinkResolutionTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("KBIndex link resolution")
struct KBIndexResolveTests {
    private let index = KBIndex(
        stemToPath: ["alex": "knowledge-base/people/alex.md", "people": "knowledge-base/people.md"],
        outByFile: [:], textByFile: [:], typeByFile: [:], vaultNames: [])

    @Test func bareStemAnyCase() {
        #expect(index.resolve("alex") == "knowledge-base/people/alex.md")
        #expect(index.resolve("Alex") == "knowledge-base/people/alex.md")
        #expect(index.resolve("ghost") == nil)
    }

    @Test func pathFormMatchesByPathSuffix() {
        #expect(index.resolve("people/alex") == "knowledge-base/people/alex.md")
        #expect(index.resolve("knowledge-base/people/alex") == "knowledge-base/people/alex.md")
        #expect(index.resolve("knowledge-base/people") == "knowledge-base/people.md")
        #expect(index.resolve("projects/alex") == nil)             // stem exists, path doesn't
    }

    @Test func anchorsAndExtension() {
        #expect(index.resolve("alex#Role") == "knowledge-base/people/alex.md")
        #expect(index.resolve("alex#^block1") == "knowledge-base/people/alex.md")
        #expect(index.resolve("alex.md") == "knowledge-base/people/alex.md")
        #expect(index.resolve("people/alex.md#Role") == "knowledge-base/people/alex.md")
    }

    @Test func anchorOnlyIsTheLinkingNote() {
        #expect(index.resolve("#Role", from: "knowledge-base/hub.md") == "knowledge-base/hub.md")
        #expect(index.resolve("#Role") == nil)
    }
}

@MainActor
@Suite("KnowledgeBaseService path and anchor links")
struct KBServicePathLinkTests {
    /// hub → `[[people/alex]]`; alex → `[[hub#Intro]]` + `[[#Role]]`;
    /// priya → `[[hub.md]]`. All resolve; none is dangling.
    private func makeKB() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbpath-\(UUID().uuidString)")
        let kb = root.appendingPathComponent("knowledge-base")
        let people = kb.appendingPathComponent("people")
        try FileManager.default.createDirectory(at: people, withIntermediateDirectories: true)
        try "Owner: [[people/alex]].".write(to: kb.appendingPathComponent("hub.md"), atomically: true, encoding: .utf8)
        try "See [[hub#Intro]] and [[#Role]].\n## Role"
            .write(to: people.appendingPathComponent("alex.md"), atomically: true, encoding: .utf8)
        try "[[hub.md]]".write(to: people.appendingPathComponent("priya.md"), atomically: true, encoding: .utf8)
        return root
    }

    @Test func pathAnchorAndExtensionLinksBecomeEdgesAndBacklinks() async throws {
        let root = try makeKB(); defer { try? FileManager.default.removeItem(at: root) }
        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()

        #expect(svc.resolveWikilink("people/alex") == "knowledge-base/people/alex.md")
        #expect(svc.outgoingLinks(for: "knowledge-base/hub.md")
                == [KBLink(target: "people/alex", resolved: "knowledge-base/people/alex.md")])
        #expect(svc.graphStats().links == 2)                       // hub–alex, hub–priya
        #expect(Set(svc.backlinks(for: "knowledge-base/hub.md").map(\.path))
                == ["knowledge-base/people/alex.md", "knowledge-base/people/priya.md"])
        let toAlex = svc.backlinks(for: "knowledge-base/people/alex.md")
        #expect(toAlex.map(\.path) == ["knowledge-base/hub.md"])
        #expect(toAlex.first?.excerpt == "Owner: [[people/alex]].")
        #expect(svc.networkStats().dangling.isEmpty)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBIndexResolveTests -only-testing:ScoutTests/KBServicePathLinkTests 2>&1 | tail -20`
Expected: compile failure — `KBIndex.resolve` and the `vaultNames:` init label don't exist. (`vaultNames` is added in Task 7. To get a behavioral RED here, add the field first: see Step 3.)

- [ ] **Step 3: Add `vaultNames` to `KBIndex` (empty for now) and the resolver**

In `KBGraph.swift`, add a stored field to `KBIndex` (no default, so the compiler flags every construction site) and update `.empty`:

```swift
    let typeByFile: [String: String]
    /// Lowercased names of every non-hidden file under the vault root (`.md`
    /// by stem, others by full name). Lets the dangling check tell a link to
    /// a vault file outside the KB from a genuinely missing note.
    let vaultNames: Set<String>
    static let empty = KBIndex(stemToPath: [:], outByFile: [:], textByFile: [:], typeByFile: [:],
                               vaultNames: [])
```

In `KnowledgeBaseService.buildIndex`, pass `vaultNames: []` for now (filled in Task 7).

Then add after `KBIndex`:

```swift

extension KBIndex {
    /// The lookup key for a `[[target]]`: any `#heading` / `#^block` anchor
    /// and a trailing `.md` removed, trimmed, lowercased.
    nonisolated static func linkKey(_ target: String) -> String {
        var key = target
        if let hash = key.firstIndex(of: "#") { key = String(key[..<hash]) }
        key = key.trimmingCharacters(in: .whitespaces).lowercased()
        if key.hasSuffix(".md") { key.removeLast(3) }
        return key
    }

    /// Resolve a `[[target]]` written in `source` to a note path, following
    /// Obsidian for the forms the vault uses: a bare stem (`[[alex]]`), a
    /// path matched as a suffix of the note's path (`[[people/alex]]`), a
    /// heading/block anchor (`[[alex#Role]]`; `[[#Role]]` is `source`
    /// itself) and an explicit `.md`. Nil when nothing matches.
    func resolve(_ target: String, from source: String? = nil) -> String? {
        let key = Self.linkKey(target)
        if key.isEmpty {
            return target.trimmingCharacters(in: .whitespaces).hasPrefix("#") ? source : nil
        }
        guard let slash = key.lastIndex(of: "/") else { return stemToPath[key] }
        guard let path = stemToPath[String(key[key.index(after: slash)...])] else { return nil }
        let bare = (path.lowercased() as NSString).deletingPathExtension
        return bare == key || bare.hasSuffix("/" + key) ? path : nil
    }
}
```

Run Step 2's command again. Expected now: it compiles, and the tests **fail on assertions** (the service still uses raw `stemToPath` lookups). `KBIndexResolveTests` passes, since it exercises the new function directly.

- [ ] **Step 4: Route every lookup through `resolve`**

In `KnowledgeBaseService.swift`:

```swift
    func resolveWikilink(_ target: String) -> String? {
        index.resolve(target)
    }
```

`outgoingLinks(for:)`: `KBLink(target: $0, resolved: index.resolve($0, from: relPath))`.

`backlinks(for:)`: `targets.contains(where: { index.resolve($0, from: from) == relPath })`.

`excerpt(in:mentioning:)`: match path-form mentions too:

```swift
    private static func excerpt(in text: String, mentioning stem: String) -> String {
        let needles = ["[[" + stem, "/" + stem + "]]", "/" + stem + "|", "/" + stem + "\\|", "/" + stem + "#"]
        let line = text.components(separatedBy: "\n").first { line in
            let lower = line.lowercased()
            return needles.contains { lower.contains($0) }
        }
        return (line ?? "").trimmingCharacters(in: .whitespaces).prefix(140).description
    }
```

`undirectedEdges()`: `guard let to = index.resolve(t, from: from), to != from else { continue }`.

`computeNetworkStats` dangling loop (named `networkStats` until Task 8): `for t in targets where index.resolve(t, from: source) == nil {`.

- [ ] **Step 5: Run tests to verify they pass**

Same command as Step 2. Expected: PASS — 5 tests in 2 suites. Then run the KB suites that cover the changed call sites:
`xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBServiceGraphTests -only-testing:ScoutTests/KBFullGraphTests -only-testing:ScoutTests/KnowledgeBaseServiceHubGraphTests -only-testing:ScoutTests/KnowledgeBaseServiceLifecycleTests -only-testing:ScoutTests/KBNetworkStatsTests 2>&1 | tail -5`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add Scout/KnowledgeBase/Models/KBGraph.swift Scout/KnowledgeBase/KnowledgeBaseService.swift ScoutTests/KnowledgeBase/KBLinkResolutionTests.swift
git commit -m "fix(kb): resolve [[path/note]], [[note#heading]] and [[note.md]] links everywhere"
```

---

## Task 7: Dangling = truly missing (ticket ids and vault files excluded)

**Files:**
- Modify: `Scout/KnowledgeBase/Models/KBGraph.swift` (`KBIndex` extension)
- Modify: `Scout/KnowledgeBase/KnowledgeBaseService.swift` (`buildIndex`, new `vaultFileNames(under:)`, dangling check)
- Test: `ScoutTests/KnowledgeBase/KBLinkResolutionTests.swift`

**Interfaces:**
- Produces: `static KBIndex.isTicketID(_:) -> Bool`, `KBIndex.existsInVault(_:) -> Bool`, `nonisolated static KnowledgeBaseService.vaultFileNames(under:) -> Set<String>`.

- [ ] **Step 1: Write the failing tests**

Append to `KBLinkResolutionTests.swift`:

```swift

@Suite("KB dangling exclusions")
struct KBDanglingExclusionTests {
    @Test func ticketIDs() {
        #expect(KBIndex.isTicketID("PROJ-1234"))
        #expect(KBIndex.isTicketID("OPS-7"))
        #expect(!KBIndex.isTicketID("proj-1234"))                  // lowercase → a note name
        #expect(!KBIndex.isTicketID("day-1"))
        #expect(!KBIndex.isTicketID("PROJ-12a"))
    }

    @Test func vaultNamesMatchByLastPathComponent() {
        let index = KBIndex(stemToPath: [:], outByFile: [:], textByFile: [:], typeByFile: [:],
                            vaultNames: ["action-items-2020-01-05", "chart-x.png"])
        #expect(index.existsInVault("action-items-2020-01-05"))
        #expect(index.existsInVault("action-items/Action-Items-2020-01-05#Morning"))
        #expect(index.existsInVault("chart-x.png"))
        #expect(!index.existsInVault("ghost"))
        #expect(!index.existsInVault("#Role"))
    }

    @Test func vaultFileNamesSkipHiddenAndIgnoredDirs() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbvault-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        for dir in ["notes", ".hidden", "node_modules"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(dir),
                                                    withIntermediateDirectories: true)
        }
        try "".write(to: root.appendingPathComponent("notes/fresh-note.md"), atomically: true, encoding: .utf8)
        try "".write(to: root.appendingPathComponent("chart-x.png"), atomically: true, encoding: .utf8)
        try "".write(to: root.appendingPathComponent(".hidden/secret.md"), atomically: true, encoding: .utf8)
        try "".write(to: root.appendingPathComponent("node_modules/dep.md"), atomically: true, encoding: .utf8)
        #expect(KnowledgeBaseService.vaultFileNames(under: root) == ["fresh-note", "chart-x.png"])
    }
}

@MainActor
@Suite("KnowledgeBaseService dangling links")
struct KBServiceDanglingTests {
    @Test func onlyTrulyMissingTargetsAreDangling() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("kbdangle-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let kb = root.appendingPathComponent("knowledge-base")
        let daily = root.appendingPathComponent("action-items")
        try FileManager.default.createDirectory(at: kb, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: daily, withIntermediateDirectories: true)
        try "".write(to: daily.appendingPathComponent("action-items-2020-01-05.md"), atomically: true, encoding: .utf8)
        try "[[action-items-2020-01-05]] [[PROJ-1234]] [[people/ghost]]"
            .write(to: kb.appendingPathComponent("hub.md"), atomically: true, encoding: .utf8)

        let svc = KnowledgeBaseService(scoutDirectory: root, fileEvents: NoopFS())
        await svc.reparseAndWait()
        #expect(svc.networkStats().dangling
                == [KBDanglingLink(source: "knowledge-base/hub.md", target: "people/ghost")])
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBDanglingExclusionTests -only-testing:ScoutTests/KBServiceDanglingTests 2>&1 | tail -20`
Expected: compile failure — `isTicketID` / `existsInVault` / `vaultFileNames` don't exist.

- [ ] **Step 3: Implement**

In `KBGraph.swift`, extend the `KBIndex` extension:

```swift

    /// A `[[PROJ-1234]]`-style issue id. The vault links tickets by id on
    /// purpose; with no note behind them they are references, not breakage.
    nonisolated static func isTicketID(_ target: String) -> Bool {
        target.range(of: #"^[A-Z][A-Z0-9]{1,9}-\d+$"#, options: .regularExpression) != nil
    }

    /// True when the link names a file elsewhere in the vault (e.g.
    /// `[[action-items-2026-04-27]]`). Obsidian resolves those, but the
    /// KB-only index can't, so they mustn't count as dangling.
    func existsInVault(_ target: String) -> Bool {
        let key = Self.linkKey(target)
        guard let name = key.split(separator: "/").last else { return false }
        return vaultNames.contains(String(name))
    }
```

In `KnowledgeBaseService.swift`, add after `relativePath(of:in:)`:

```swift

    /// Lowercased names of every non-hidden file under `scoutDirectory`
    /// (`.md` by stem, others by full name), skipping `ignoredNames`.
    /// Filenames only — no file is read.
    nonisolated static func vaultFileNames(under scoutDirectory: URL) -> Set<String> {
        guard let walker = FileManager.default.enumerator(
            at: scoutDirectory, includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles, .skipsPackageDescendants]) else { return [] }
        var names = Set<String>()
        while let url = walker.nextObject() as? URL {
            if ignoredNames.contains(url.lastPathComponent) { walker.skipDescendants(); continue }
            guard (try? url.resourceValues(forKeys: [.isRegularFileKey]))?.isRegularFile == true else { continue }
            let name = url.lastPathComponent.lowercased()
            names.insert(url.pathExtension.lowercased() == "md" ? (name as NSString).deletingPathExtension : name)
        }
        return names
    }
```

In `buildIndex`, replace `vaultNames: []` with `vaultNames: vaultFileNames(under: scoutDirectory)`.

In the dangling loop:

```swift
            for t in targets where index.resolve(t, from: source) == nil
                && !KBIndex.isTicketID(t) && !index.existsInVault(t) {
```

and update its comment to say ticket ids and vault files outside the KB are excluded.

- [ ] **Step 4: Run tests to verify they pass**

Same command as Step 2. Expected: PASS — 4 tests in 2 suites. Also re-run `-only-testing:ScoutTests/KBNetworkStatsTests` (its `sam → ghost` link must stay dangling). Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Scout/KnowledgeBase/Models/KBGraph.swift Scout/KnowledgeBase/KnowledgeBaseService.swift ScoutTests/KnowledgeBase/KBLinkResolutionTests.swift
git commit -m "feat(kb): dangling counts only truly missing notes (skip ticket ids, vault files)"
```

---

## Task 8: Memoize `networkStats()` per reparse

**Files:**
- Modify: `Scout/KnowledgeBase/KnowledgeBaseService.swift`
- Test: `ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift`

Measured on the real vault (Debug): `networkStats()` ≈ 78 ms vs `graphStats()` 14 ms, paid on every overview body eval. Cache it per `(generation, hubCap)`, where `generation` bumps whenever `tree` or `index` is replaced.

- [ ] **Step 1: Write the invalidation guard test**

Append inside `KBNetworkStatsTests`:

```swift

    @Test func statsRecomputeAfterReparseAndPerHubCap() async throws {
        let (svc, root) = try await load(Self.mainland)
        defer { try? FileManager.default.removeItem(at: root) }
        let before = svc.networkStats()
        #expect(svc.networkStats() == before)
        try "[[lonely]]".write(to: root.appendingPathComponent("knowledge-base/fresh-note.md"),
                               atomically: true, encoding: .utf8)
        await svc.reparseAndWait()
        let after = svc.networkStats()
        #expect(after.noteCount == 8)
        #expect(!after.orphans.contains("knowledge-base/lonely.md"))
        #expect(svc.networkStats(hubCap: 1).topHubs.count == 1)    // hubCap is part of the key
    }
```

This passes before the cache exists (no cache, no staleness). It's the guard that the cache must keep green, not a RED. A cache keyed without `generation` or without `hubCap` turns it red.

- [ ] **Step 2: Run it (expect PASS — guard established)**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBNetworkStatsTests 2>&1 | tail -5`
Expected: PASS — 9 tests.

- [ ] **Step 3: Implement the cache**

In `KnowledgeBaseService`:

```swift
    @Published private(set) var tree: [KBNode] = [] { didSet { generation &+= 1 } }
    ...
    @Published private(set) var index: KBIndex = .empty { didSet { generation &+= 1 } }

    /// Bumped whenever `tree` or `index` is replaced; keys per-reparse caches.
    private var generation = 0
    private var networkStatsCache: (generation: Int, hubCap: Int, stats: KBNetworkStats)?
```

Rename the existing `networkStats(hubCap:)` to `private func computeNetworkStats(hubCap: Int) -> KBNetworkStats` and add:

```swift
    /// One-pass network analysis for the overview, cached until the next
    /// reparse (the overview evaluates it on every body pass).
    func networkStats(hubCap: Int = 20) -> KBNetworkStats {
        if let c = networkStatsCache, c.generation == generation, c.hubCap == hubCap { return c.stats }
        let stats = computeNetworkStats(hubCap: hubCap)
        networkStatsCache = (generation, hubCap, stats)
        return stats
    }
```

Move the doc comment's first paragraph onto `computeNetworkStats`.

- [ ] **Step 4: Prove the guard bites, then pass**

Temporarily drop `c.generation == generation` from the cache check and run Step 2's command. Expected: `statsRecomputeAfterReparseAndPerHubCap` FAILS (stale stats). Restore it and run again. Expected: PASS — 9 tests.

- [ ] **Step 5: Commit**

```bash
git add Scout/KnowledgeBase/KnowledgeBaseService.swift ScoutTests/KnowledgeBase/KBNetworkStatsTests.swift
git commit -m "perf(kb): memoize networkStats per reparse"
```

---

## Task 9: Full verification + coverage floor

- [ ] **Step 1: Full test target with a result bundle (as CI runs it)**

Run:
```bash
rm -rf TestResults.xcresult
xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests -resultBundlePath TestResults.xcresult 2>&1 | tail -15
```
Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 2: Coverage floor**

Run: `scripts/check-coverage.sh TestResults.xcresult`
Expected: exit 0, coverage ≥ the floor. A local run reads ~0.5–1 point above CI (see the script header), so treat a local margin under ~1 point as a warning. Don't edit `coverage-floor.txt` from a local number.

- [ ] **Step 3: Literal re-check**

If any test literal changed from this plan, re-run the vault check in repo `CLAUDE.md` (exclude `~/Scout/.claude/`; expect 0 file hits for invented names).

- [ ] **Step 4: Real-vault re-check (scratch, never committed)**

Re-render `KBStatsView` over `~/Scout` with a throwaway `ImageRenderer` test, then delete the file. Confirm dangling has dropped from 6,289 to roughly the genuinely missing set, and that the link total has grown by about the 2,389 path links. Keep the PNG local: it shows real names.

---

## Self-Review

**1. Spec coverage** (against `2026-07-07-kb-network-stats-design.md`):
- All 4 health metrics (dangling, orphans, islands, weakly-linked) → Task 2 (engine) + Task 4 (view). ✓
- All 4 insight metrics (top hubs, degree summary, per-type, components) → Task 2 + Task 3 (strings) + Task 5. ✓
- Count + top-5 + "Show all" disclosure; "✓ none" empty state → Tasks 3/4/5. ✓
- Click-to-open on every note/hub/island item → `onOpen` throughout. ✓
- One pass, no new I/O; overview drops its separate `graphStats()` pass → Task 2 + Task 4 Step 2. ✓
- Exact definitions (orphan/weak/dangling/island/hub) + determinism incl. equal-size components → encoded + tested in Task 2. ✓
- Single adjacency source shared with feature 2's code → Task 1. ✓
- Empty vault + case-variant links → Task 2 tests. ✓

**2. Placeholder scan:** No "TBD", "handle edge cases" or "similar to". Every code step is complete, and every `DS`/layout symbol was verified to exist on `main` @ `7a037c9`.

**3. Type consistency:** `adjacency(of:)`, `networkStats(hubCap:)`, `KBNetworkStats` fields (incl. `noteCount`/`linkCount`), `KBDanglingLink`/`KBHub`/`KBTypeCount`, `degreeSummary`/`componentsSummary`/`healthTitle`, and `KBStatsView(stats:onOpen:)` are named identically across tasks.

## Notes for the implementer

- **Hub ordering** matches `KBGraph.topHubs(maxNodes:)` (`KBGraph.swift:107`): degree desc, id asc. Keep them identical.
- **Recompute cost:** profiling on the real vault showed churn (≈78 ms Debug per body eval), so Task 8 memoizes per reparse.
- **Vault listing:** Task 7's `vaultFileNames` is the one deliberate exception to "no new disk I/O". It lists filenames (about 1,200 under `~/Scout`) during the existing off-main reparse and reads no file.
- **`fullGraph()`** keeps its own degree map. Moving it onto `adjacency(of:)` is out of scope (no behavior change, no need).
