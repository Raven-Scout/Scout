import SwiftUI
import Grape

/// Force-directed graph rendered with Grape (native d3-force for SwiftUI). Shared
/// by the per-note local graph (right panel) and the whole-KB global graph
/// (overview). Tapping a node navigates to it; drag pans, pinch zooms.
struct KBGraphCanvas: View {
    let graph: KBGraph
    let onNavigate: (String) -> Void
    /// Only label nodes at/above this degree (keeps a dense global graph legible).
    /// The center is always labeled.
    var labelMinDegree: Int = 1

    @State private var state = ForceDirectedGraphState(initialIsRunning: true)

    var body: some View {
        ForceDirectedGraph(states: state) {
            Series(graph.nodes) { node in
                NodeMark(id: node.id)
                    .symbol(Circle())
                    .symbolSize(radius: radius(node))
                    .foregroundStyle(node.group.color)
                    .stroke()
                    .annotation(node.label, alignment: .bottom, offset: .init(dx: 0, dy: 1)) {
                        if node.isCenter || node.degree >= labelMinDegree {
                            Text(node.label)
                                .font(DS.sans(node.isCenter ? 10 : 9,
                                              weight: node.isCenter ? .semibold : .regular))
                                .foregroundStyle(DS.Ink.p2)
                                .lineLimit(1)
                        }
                    }
            }
            Series(graph.edges) { edge in
                LinkMark(from: edge.from, to: edge.to)
            }
        } force: {
            .manyBody(strength: -28)
            .center()
            .link(originalLength: 42, stiffness: .weightedByDegree { _, _ in 1.0 })
        }
        .graphOverlay { proxy in
            Rectangle().fill(.clear).contentShape(Rectangle())
                .withGraphDragGesture(proxy, of: String.self)
                .withGraphMagnifyGesture(proxy)
                .withGraphTapGesture(proxy, of: String.self) { onNavigate($0) }
        }
    }

    private func radius(_ node: KBGraphNode) -> Double {
        node.isCenter ? 9 : max(4, min(10, 4 + Double(node.degree)))
    }
}

/// Legend of entity-type colors present in a graph.
struct KBGraphLegend: View {
    let groups: [KBEntityGroup]
    var body: some View {
        FlowLayout(spacing: 8) {
            ForEach(groups, id: \.self) { g in
                HStack(spacing: 4) {
                    Circle().fill(g.color).frame(width: 7, height: 7)
                    Text(g.label).font(DS.sans(9.5)).foregroundStyle(DS.Ink.p3)
                }
            }
        }
        .padding(.horizontal, 8)
    }
}

/// The per-note neighbourhood graph for the right panel, with empty state.
struct KBLocalGraphView: View {
    let graph: KBGraph
    let onNavigate: (String) -> Void

    var body: some View {
        VStack(spacing: 8) {
            if graph.nodes.count <= 1 {
                emptyState
            } else {
                KBGraphCanvas(graph: graph, onNavigate: onNavigate)
                KBGraphLegend(groups: presentGroups)
            }
        }
    }

    private var presentGroups: [KBEntityGroup] {
        Array(Set(graph.nodes.map(\.group))).sorted { $0.label < $1.label }
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Image(systemName: "point.3.connected.trianglepath.dotted")
                .font(.system(size: 22)).foregroundStyle(DS.Ink.p4)
            Text("No linked notes")
                .font(DS.sans(11)).foregroundStyle(DS.Ink.p4)
            Text("This note has no [[wikilinks]] to or from other notes.")
                .font(DS.sans(10)).foregroundStyle(DS.Ink.p4)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(16)
    }
}
