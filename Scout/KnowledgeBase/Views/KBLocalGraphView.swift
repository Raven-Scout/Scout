import SwiftUI

/// A native force-directed view of the neighbourhood around the selected note.
/// Edges are drawn with `Canvas`; nodes are positioned tappable buttons (so hit
/// testing and navigation are trivial). Drag to pan, pinch to zoom; tapping a
/// node navigates to it (which re-centres the graph).
struct KBLocalGraphView: View {
    let graph: KBGraph
    let onNavigate: (String) -> Void

    /// Normalized [0,1] node positions, recomputed when the node set changes.
    @State private var positions: [String: CGPoint] = [:]
    @State private var layoutKey: String = ""
    @State private var zoom: CGFloat = 1
    @GestureState private var pinch: CGFloat = 1
    @State private var pan: CGSize = .zero
    @GestureState private var drag: CGSize = .zero

    var body: some View {
        VStack(spacing: 8) {
            if graph.nodes.count <= 1 {
                emptyState
            } else {
                GeometryReader { geo in
                    let inset: CGFloat = 28
                    let size = CGSize(width: geo.size.width - inset * 2,
                                      height: geo.size.height - inset * 2)
                    ZStack {
                        edgeCanvas(in: geo.size, inset: inset)
                        ForEach(graph.nodes) { node in
                            if let p = positions[node.id] {
                                nodeView(node)
                                    .position(x: inset + p.x * size.width,
                                              y: inset + p.y * size.height)
                            }
                        }
                    }
                    .scaleEffect(zoom * pinch)
                    .offset(x: pan.width + drag.width, y: pan.height + drag.height)
                    .contentShape(Rectangle())
                    .gesture(
                        DragGesture()
                            .updating($drag) { v, s, _ in s = v.translation }
                            .onEnded { v in pan.width += v.translation.width; pan.height += v.translation.height }
                    )
                    .gesture(
                        MagnificationGesture()
                            .updating($pinch) { v, s, _ in s = v }
                            .onEnded { v in zoom = min(3, max(0.5, zoom * v)) }
                    )
                    .clipped()
                }
                legend
            }
        }
        .onAppear { recomputeIfNeeded() }
        .onChange(of: nodeSetKey) { _, _ in recomputeIfNeeded() }
    }

    private var nodeSetKey: String {
        graph.nodes.map(\.id).sorted().joined(separator: "|")
    }

    private func recomputeIfNeeded() {
        let key = nodeSetKey
        guard key != layoutKey else { return }
        positions = Self.computeLayout(graph)
        layoutKey = key
        zoom = 1; pan = .zero
    }

    // MARK: - Pieces

    private func edgeCanvas(in size: CGSize, inset: CGFloat) -> some View {
        Canvas { ctx, _ in
            let w = size.width - inset * 2, h = size.height - inset * 2
            for edge in graph.edges {
                guard let a = positions[edge.from], let b = positions[edge.to] else { continue }
                var path = Path()
                path.move(to: CGPoint(x: inset + a.x * w, y: inset + a.y * h))
                path.addLine(to: CGPoint(x: inset + b.x * w, y: inset + b.y * h))
                ctx.stroke(path, with: .color(DS.Rule.hard), lineWidth: 0.75)
            }
        }
    }

    private func nodeView(_ node: KBGraphNode) -> some View {
        let diameter = node.isCenter ? 16.0 : min(14, 7 + Double(node.degree))
        return Button { onNavigate(node.id) } label: {
            VStack(spacing: 2) {
                Circle()
                    .fill(node.group.color)
                    .frame(width: diameter, height: diameter)
                    .overlay(Circle().strokeBorder(node.isCenter ? DS.Ink.p1 : .clear, lineWidth: 1.5))
                Text(node.label)
                    .font(DS.sans(node.isCenter ? 10 : 9, weight: node.isCenter ? .semibold : .regular))
                    .foregroundStyle(node.isCenter ? DS.Ink.p1 : DS.Ink.p2)
                    .lineLimit(1).fixedSize()
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(node.label)
    }

    private var legend: some View {
        let groups = Array(Set(graph.nodes.map(\.group))).sorted { $0.label < $1.label }
        return FlowLayout(spacing: 8) {
            ForEach(groups, id: \.self) { g in
                HStack(spacing: 4) {
                    Circle().fill(g.color).frame(width: 7, height: 7)
                    Text(g.label).font(DS.sans(9.5)).foregroundStyle(DS.Ink.p3)
                }
            }
        }
        .padding(.horizontal, 8)
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

    // MARK: - Force layout

    /// Fruchterman–Reingold layout in the unit square. Deterministic (circle
    /// seed, no RNG) so the graph doesn't jitter between renders.
    static func computeLayout(_ graph: KBGraph, iterations: Int = 220) -> [String: CGPoint] {
        let ids = graph.nodes.map(\.id)
        let n = ids.count
        guard n > 1 else { return n == 1 ? [ids[0]: CGPoint(x: 0.5, y: 0.5)] : [:] }

        let k = sqrt(1.0 / Double(n))            // ideal edge length
        let centerID = graph.nodes.first(where: \.isCenter)?.id

        var pos: [String: CGPoint] = [:]
        for (i, id) in ids.enumerated() {
            let angle = 2 * Double.pi * Double(i) / Double(n)
            pos[id] = CGPoint(x: 0.5 + 0.30 * cos(angle), y: 0.5 + 0.30 * sin(angle))
        }
        if let centerID { pos[centerID] = CGPoint(x: 0.5, y: 0.5) }

        var temp = 0.12
        let cooling = temp / Double(iterations + 1)

        for _ in 0..<iterations {
            var disp: [String: CGVector] = Dictionary(uniqueKeysWithValues: ids.map { ($0, .zero) })

            // Repulsion between all pairs.
            for i in 0..<n {
                for j in (i + 1)..<n {
                    let a = ids[i], b = ids[j]
                    var dx = pos[a]!.x - pos[b]!.x
                    var dy = pos[a]!.y - pos[b]!.y
                    var dist = sqrt(dx * dx + dy * dy)
                    if dist < 0.0001 { dx = 0.001 * Double(i + 1); dy = 0.001 * Double(j + 1); dist = sqrt(dx*dx+dy*dy) }
                    let force = (k * k) / dist
                    let ux = dx / dist, uy = dy / dist
                    disp[a]!.dx += ux * force; disp[a]!.dy += uy * force
                    disp[b]!.dx -= ux * force; disp[b]!.dy -= uy * force
                }
            }
            // Attraction along edges.
            for edge in graph.edges {
                guard let pa = pos[edge.from], let pb = pos[edge.to] else { continue }
                let dx = pa.x - pb.x, dy = pa.y - pb.y
                let dist = max(sqrt(dx * dx + dy * dy), 0.0001)
                let force = (dist * dist) / k
                let ux = dx / dist, uy = dy / dist
                disp[edge.from]!.dx -= ux * force; disp[edge.from]!.dy -= uy * force
                disp[edge.to]!.dx += ux * force; disp[edge.to]!.dy += uy * force
            }
            // Apply, capped by temperature; keep center pinned.
            for id in ids {
                if id == centerID { continue }
                let d = disp[id]!
                let len = max(sqrt(d.dx * d.dx + d.dy * d.dy), 0.0001)
                let step = min(len, temp)
                var x = pos[id]!.x + (d.dx / len) * step
                var y = pos[id]!.y + (d.dy / len) * step
                x = min(0.98, max(0.02, x)); y = min(0.98, max(0.02, y))
                pos[id] = CGPoint(x: x, y: y)
            }
            temp = max(temp - cooling, 0.001)
        }
        return normalize(pos)
    }

    /// Rescale positions to fill the [0.05, 0.95] box so the graph uses the
    /// available area regardless of how the simulation settled.
    private static func normalize(_ pos: [String: CGPoint]) -> [String: CGPoint] {
        let xs = pos.values.map(\.x), ys = pos.values.map(\.y)
        guard let minX = xs.min(), let maxX = xs.max(),
              let minY = ys.min(), let maxY = ys.max() else { return pos }
        let spanX = max(maxX - minX, 0.0001), spanY = max(maxY - minY, 0.0001)
        var out: [String: CGPoint] = [:]
        for (id, p) in pos {
            out[id] = CGPoint(x: 0.05 + (p.x - minX) / spanX * 0.90,
                              y: 0.05 + (p.y - minY) / spanY * 0.90)
        }
        return out
    }
}
