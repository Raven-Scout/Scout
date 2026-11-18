import Foundation

/// What the web knowledge map (`kb-map.html`) is given to draw: every note as
/// a node, every wikilink between two of them as a link, and the groups in
/// legend order with their counts. Built from a `KBGraph`; colours are not in
/// here — the page reads each group's `--g-<group>` token from the theme.
nonisolated struct KBMapPayload: Encodable, Equatable {
    struct Node: Encodable, Equatable {
        let id: String          // repo-relative path
        let label: String
        let group: String       // KBEntityGroup raw value
        let degree: Int
        /// The note's folder under the KB root ("people", "projects/kai"), "" at the root.
        let folder: String
        /// Last modification, seconds since 1970; 0 when unknown.
        let modified: Double
        /// What VoiceOver reads for the node.
        let a11y: String
    }
    struct Group: Encodable, Equatable {
        let key: String
        let label: String
        let count: Int
    }

    let nodes: [Node]
    /// Pairs of indices into `nodes`.
    let links: [[Int]]
    let groups: [Group]
    /// The note a local map is centred on; nil for the whole-vault map.
    let center: String?

    static let empty = KBMapPayload(nodes: [], links: [], groups: [], center: nil)

    /// `modified` maps a node id to its modification time; missing ids get 0.
    static func build(from graph: KBGraph, center: String? = nil,
                      modified: [String: Date] = [:]) -> KBMapPayload {
        let nodes = graph.nodes.map { n in
            Node(id: n.id, label: n.label, group: n.group.rawValue, degree: n.degree,
                 folder: folder(of: n.id),
                 modified: modified[n.id]?.timeIntervalSince1970 ?? 0,
                 a11y: accessibilityLabel(for: n, isCenter: n.id == center || n.isCenter))
        }
        var position: [String: Int] = [:]
        for (i, n) in nodes.enumerated() { position[n.id] = i }
        var pairs: [(Int, Int)] = []
        for e in graph.edges {
            guard let a = position[e.from], let b = position[e.to], a != b else { continue }
            pairs.append((min(a, b), max(a, b)))
        }
        pairs.sort { $0 < $1 }
        let links: [[Int]] = pairs.map { [$0.0, $0.1] }
        var counts: [KBEntityGroup: Int] = [:]
        for n in graph.nodes { counts[n.group, default: 0] += 1 }
        let groups = KBEntityGroup.allCases.compactMap { g -> Group? in
            guard let c = counts[g], c > 0 else { return nil }
            return Group(key: g.rawValue, label: g.label, count: c)
        }
        return KBMapPayload(nodes: nodes, links: links, groups: groups, center: center)
    }

    /// "people" for `knowledge-base/people/alex.md`; "" for a root note.
    static func folder(of path: String) -> String {
        var parts = path.split(separator: "/").map(String.init)
        if parts.first == "knowledge-base" { parts.removeFirst() }
        return parts.dropLast().joined(separator: "/")
    }

    // MARK: - Accessibility

    /// "Alpha, 1 link" / "Center, this note, 2 links".
    static func accessibilityLabel(for node: KBGraphNode, isCenter: Bool? = nil) -> String {
        let links = "\(node.degree) link\(node.degree == 1 ? "" : "s")"
        return (isCenter ?? node.isCenter) ? "\(node.label), this note, \(links)" : "\(node.label), \(links)"
    }

    /// At most this many notes in VoiceOver's list of the map. The page lists
    /// them as buttons (visually hidden) in this order.
    static let accessibleNodeLimit = 60

    /// The order VoiceOver walks the map's nodes in: the centre first, then
    /// by degree (most connected first), then by name; capped.
    static func accessibleNodes(_ graph: KBGraph) -> [KBGraphNode] {
        Array(graph.nodes.sorted { a, b in
            if a.isCenter != b.isCenter { return a.isCenter }
            if a.degree != b.degree { return a.degree > b.degree }
            return a.label.localizedStandardCompare(b.label) == .orderedAscending
        }.prefix(accessibleNodeLimit))
    }
}
