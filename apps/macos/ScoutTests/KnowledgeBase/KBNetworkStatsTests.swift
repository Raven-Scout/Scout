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
            let url = kb.appendingPathComponent(name)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            try body.write(to: url, atomically: true, encoding: .utf8)
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

    // MARK: Archive copies and paperwork are set aside

    /// The mainland plus an archived snapshot full of broken and cross links,
    /// and personal paperwork with no links — the shape that swamped a real vault's
    /// health rows.
    private static let withSetAside: [String: String] = mainland.merging([
        "projects/demo/archive/2026-01-snapshot.md": "[[hub]] [[gone-1]] [[gone-2]] [[gone-3]]",
        "people/archived/old-contact.md": "[[ghost-two]]",
        "personal/receipts/2026-03-lamp.md": "paid",
        "personal/home/2026-02-invoice.md": "paid",
        "personal/home/plumber-visit.md": "---\ntype: invoice-scan\n---\npaid",
        "personal/home/2026-05-boiler-certificate.md": "issued",
    ]) { a, _ in a }

    @Test func archivedAndPaperworkNotesAreListedApart() async throws {
        let s = try await stats(Self.withSetAside)
        #expect(s.archived == [
            "knowledge-base/people/archived/old-contact.md",
            "knowledge-base/projects/demo/archive/2026-01-snapshot.md",
        ])
        #expect(s.paperwork == [
            "knowledge-base/personal/home/2026-02-invoice.md",
            "knowledge-base/personal/home/2026-05-boiler-certificate.md",
            "knowledge-base/personal/home/plumber-visit.md",
            "knowledge-base/personal/receipts/2026-03-lamp.md",
        ])
        #expect(s.setAsideSummary == "Not counted: 2 archived notes · 4 paperwork notes")
    }

    @Test func healthAndInsightCoverLiveNotesOnly() async throws {
        let s = try await stats(Self.withSetAside)
        let live = try await stats(Self.mainland)
        // The archived snapshot's broken links and the paperwork's missing links don't count.
        #expect(s.dangling == live.dangling)
        #expect(s.orphans == live.orphans)
        #expect(s.weaklyLinked == live.weaklyLinked)
        #expect(s.islands == live.islands)
        // Its link to the hub doesn't make the hub bigger, or the snapshot a hub.
        #expect(s.topHubs == live.topHubs)
        #expect(s.avgDegree == live.avgDegree)
        #expect(s.byType.reduce(0) { $0 + $1.count } == 7)
        // Totals still count every note and link.
        #expect(s.noteCount == 13)
        #expect(s.linkCount == live.linkCount + 1)
    }

    @Test func aVaultWithNothingSetAsideHasNoSummary() async throws {
        let s = try await stats(Self.mainland)
        #expect(s.archived.isEmpty && s.paperwork.isEmpty)
        #expect(s.setAsideSummary == nil)
    }

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
}

@Suite("KBNoteKind")
struct KBNoteKindTests {
    @Test(arguments: [
        ("knowledge-base/projects/demo/archive/snapshot.md", KBNoteKind.archived),
        ("knowledge-base/people/Archived/old.md", .archived),
        ("knowledge-base/archive/anything.md", .archived),
        ("knowledge-base/receipts/lamp.md", .paperwork),
        ("knowledge-base/personal/2026-02-invoice.md", .paperwork),
        ("knowledge-base/personal/Invoices/march.md", .paperwork),
        ("knowledge-base/projects/2026-02-invoice.md", .paperwork),
        // The wider words count under a `personal` folder, at any depth…
        ("knowledge-base/personal/home/boiler-certificate.md", .paperwork),
        ("knowledge-base/personal/Estimates/roof.md", .paperwork),
        ("knowledge-base/personal/2026-laptop-warranty.md", .paperwork),
        ("knowledge-base/personal/payslips/march.md", .paperwork),
        // …and nowhere else: in a work note they're live knowledge.
        ("knowledge-base/projects/tls-certificate-rotation.md", .live),
        ("knowledge-base/projects/migration-estimate.md", .live),
        ("knowledge-base/projects/estimates/q3.md", .live),
        // Reports are live notes, personal or not.
        ("knowledge-base/personal/2026-health-report.md", .live),
        ("knowledge-base/people/alex.md", .live),
        // A file *named* archive is a live note; only an archive folder sets notes aside.
        ("knowledge-base/linear-issues-archive.md", .live),
        // Whole words only: these merely contain the letters.
        ("knowledge-base/projects/receiptless-checkout.md", .live),
        ("knowledge-base/projects/archiver.md", .live),
        // Folders match whole: a folder that merely has the word in it is not a receipts folder.
        ("knowledge-base/projects/receipt-scanner/overview.md", .live),
        ("knowledge-base/archived-ideas/overview.md", .live),
    ])
    func byPath(_ path: String, _ kind: KBNoteKind) {
        #expect(KBNoteKind.of(path) == kind)
    }

    @Test func frontmatterTypeMarksPaperwork() {
        #expect(KBNoteKind.of("knowledge-base/personal/plumber-visit.md", type: "invoice-scan") == .paperwork)
        #expect(KBNoteKind.of("knowledge-base/personal/plumber-visit.md", type: "receipt") == .paperwork)
        #expect(KBNoteKind.of("knowledge-base/personal/boiler.md", type: "certificate") == .paperwork)
        #expect(KBNoteKind.of("knowledge-base/projects/tls.md", type: "certificate") == .live)
        #expect(KBNoteKind.of("knowledge-base/people/alex.md", type: "person") == .live)
    }

    @Test func archivedPaperworkCountsAsArchived() {
        #expect(KBNoteKind.of("knowledge-base/archive/receipts/lamp.md") == .archived)
    }
}

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

    /// "Show all" expands at most `cap` more items; the rest become a count,
    /// so a ~1,300-item list never renders ~1,300 buttons.
    @Test func disclosureWindowCapsTheExpandedList() {
        let big = KBNetworkStats.disclosureWindow(count: 1314, topN: 5, cap: 200)
        #expect(big.shown == 5..<205)
        #expect(big.hidden == 1109)
        let small = KBNetworkStats.disclosureWindow(count: 12, topN: 5, cap: 200)
        #expect(small.shown == 5..<12)
        #expect(small.hidden == 0)
        let none = KBNetworkStats.disclosureWindow(count: 3, topN: 5, cap: 200)
        #expect(none.shown.isEmpty)
        #expect(none.hidden == 0)
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
