// ScoutTests/KnowledgeBase/KBGraphTransformTests.swift
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
        #expect(hubs.nodes.map(\.id) == ["a", "b"])              // top 2 by degree
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
        #expect(out.edges.isEmpty)                  // p–j edge drops with j
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

@MainActor
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
        await svc.reparseAndWait()

        let g = svc.hubGraph(maxNodes: 2)
        #expect(g.nodes.count == 2)
        #expect(g.nodes.contains { $0.id == "knowledge-base/hub.md" })  // highest degree kept
    }
}
