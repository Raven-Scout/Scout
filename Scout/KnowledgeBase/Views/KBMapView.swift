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

    @State private var activeTypes: Set<KBEntityGroup> = Set(KBEntityGroup.allCases)
    @State private var hideOrphans = false
    @State private var minDegree = 0

    /// The focus path, or nil when the focused note no longer exists on disk
    /// (deleted/renamed mid-focus) — the map falls back to the hub seed rather
    /// than rendering a stale single-node graph.
    private var effectiveFocus: String? {
        guard let f = focusPath, noteExists(f) else { return nil }
        return f
    }

    private func noteExists(_ path: String) -> Bool {
        service.tree.flatMap(\.allFiles).contains { $0.relativePath == path }
    }

    private var baseGraph: KBGraph {
        if let f = effectiveFocus { return service.localGraph(around: f, depth: 2, maxNodes: maxNodes) }
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
            filterBar
            if baseGraph.nodes.isEmpty {
                // Vault-empty (no markdown notes at all) — distinct from
                // "filters removed everything" below.
                vaultEmptyState
                capCaption
            } else if shownGraph.nodes.isEmpty {
                noMatchState
            } else {
                canvas
                capCaption
                KBGraphLegend(groups: Array(Set(shownGraph.nodes.map(\.group))).sorted { $0.label < $1.label })
            }
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
            }.buttonStyle(.plain).foregroundStyle(effectiveFocus == nil ? DS.Ink.p3 : DS.Accent.ink)
            if !history.isEmpty || effectiveFocus != nil {
                Button { back() } label: { Label("Back", systemImage: "chevron.left").font(DS.sans(11)) }
                    .buttonStyle(.plain).foregroundStyle(DS.Accent.ink)
            }
            if let f = effectiveFocus {
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
                      labelMinDegree: effectiveFocus == nil ? 3 : 1, initialScale: 2.0)
            .id("\(effectiveFocus ?? "hubs")|\(typesKey)|\(hideOrphans)|\(minDegree)")
            .frame(height: 460).frame(maxWidth: 1100)
            .background(RoundedRectangle(cornerRadius: 8).fill(DS.Paper.sunk.opacity(0.4)))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
    }

    /// Canvas identity key for the type filter: the sorted set members joined,
    /// so any membership change re-seeds the layout (a count would treat two
    /// different equal-size selections as the same view).
    private var typesKey: String {
        activeTypes.map(\.rawValue).sorted().joined(separator: ",")
    }

    // MARK: filters

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

    private var vaultEmptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "point.3.connected.trianglepath.dotted")
                .font(.system(size: 22)).foregroundStyle(DS.Ink.p4)
            Text("No notes yet").font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
            Text("Notes added to the knowledge base will appear here as a map.")
                .font(DS.sans(10)).foregroundStyle(DS.Ink.p4)
        }
        .frame(maxWidth: .infinity).frame(height: 200)
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

    // MARK: actions

    private func handleTap(_ id: String) {
        if id == effectiveFocus { onOpen(id); return }   // tap the centre again → open
        focus(id)
    }
    private func focus(_ path: String) {
        if let current = effectiveFocus { history.append(current) }
        focusPath = path
        query = ""
    }
    private func back() {
        // Skip over history entries whose note has since been deleted/renamed.
        while let prev = history.popLast() {
            if noteExists(prev) { focusPath = prev; return }
        }
        focusPath = nil
    }
    private func toHubs() {
        focusPath = nil; history.removeAll()
    }
}
