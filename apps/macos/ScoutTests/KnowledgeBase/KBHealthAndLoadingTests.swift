import Foundation
import Testing
@testable import Scout

/// KB health groups dangling links by the note that holds them and explains
/// each figure (UI audit #73); the page says it's loading instead of going
/// blank or reading "0 notes" (#76).
@Suite("KB health and loading")
struct KBHealthAndLoadingTests {
    private func net(dangling: [KBDanglingLink]) -> KBNetworkStats {
        KBNetworkStats(noteCount: 0, linkCount: 0, orphans: [], weaklyLinked: [], dangling: dangling,
                       islands: [], topHubs: [], avgDegree: 0, maxDegree: 0, byType: [],
                       clusterCount: 0, largestComponentSize: 0)
    }

    @Test("dangling links group by source note, the note with most first")
    func danglingGroups() {
        let stats = net(dangling: [
            KBDanglingLink(source: "knowledge-base/a.md", target: "x"),
            KBDanglingLink(source: "knowledge-base/b.md", target: "p"),
            KBDanglingLink(source: "knowledge-base/b.md", target: "q"),
            KBDanglingLink(source: "knowledge-base/c.md", target: "y"),
        ])
        #expect(stats.danglingBySource == [
            KBDanglingGroup(source: "knowledge-base/b.md", targets: ["p", "q"]),
            KBDanglingGroup(source: "knowledge-base/a.md", targets: ["x"]),
            KBDanglingGroup(source: "knowledge-base/c.md", targets: ["y"]),
        ])
        #expect(stats.danglingTitle == "4 dangling links in 3 notes")
        #expect(net(dangling: [KBDanglingLink(source: "a.md", target: "x")]).danglingTitle
                == "1 dangling link in 1 note")
        #expect(net(dangling: []).danglingTitle == "Dangling links: ✓ none")
        #expect(net(dangling: []).danglingBySource.isEmpty)
    }

    @Test("every health figure has a one-line explanation")
    func explanations() {
        for metric in KBHealthMetric.allCases {
            #expect(!metric.explanation.isEmpty)
            #expect(!metric.explanation.contains("\n"))
        }
        #expect(Set(KBHealthMetric.allCases.map(\.explanation)).count == KBHealthMetric.allCases.count)
    }

    @Test("loading shows until the first scan lands; a rescan keeps the tree")
    func loadingRule() {
        typealias KB = KnowledgeBaseView
        #expect(KB.showsLoading(.idle, treeIsEmpty: true))
        #expect(KB.showsLoading(.loading, treeIsEmpty: true))
        // Returning to the page calls load() again: the tree stays on screen.
        #expect(!KB.showsLoading(.loading, treeIsEmpty: false))
        #expect(!KB.showsLoading(.loaded, treeIsEmpty: true))       // a real empty KB
        #expect(!KB.showsLoading(.missing(URL(fileURLWithPath: "/x")), treeIsEmpty: true))
        #expect(!KB.showsLoading(.failed("no"), treeIsEmpty: true))
    }
}
