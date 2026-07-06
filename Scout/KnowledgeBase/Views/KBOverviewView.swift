import SwiftUI

/// Landing page shown when no note is selected: KB stats plus quick-access tiles
/// to the canonical hub notes (only those that actually exist).
struct KBOverviewView: View {
    @ObservedObject var service: KnowledgeBaseService
    let onNavigate: (String) -> Void

    /// Canonical hub notes, in display order. Each resolves by its note stem
    /// through the wikilink index (so a moved hub still gets a tile), with an
    /// exact-path fallback for non-markdown files the index doesn't cover.
    /// Hubs that resolve to nothing on disk are hidden.
    private static let quickLinks: [(stem: String, fallbackPath: String, label: String, icon: String)] = [
        ("knowledge-base",  "knowledge-base/knowledge-base.md",    "Index",    "book.closed"),
        ("people",          "knowledge-base/people.md",            "People",   "person.2"),
        ("issues",          "knowledge-base/issues.md",            "Issues",   "exclamationmark.triangle"),
        ("projects",        "knowledge-base/projects/projects.md", "Projects", "folder"),
        ("channels",        "knowledge-base/channels.md",          "Channels", "number"),
        ("research-queue",  "knowledge-base/research-queue.md",    "Research", "magnifyingglass"),
        ("review-queue",    "knowledge-base/review-queue.md",      "Review",   "checkmark.circle"),
        ("schema",          "knowledge-base/ontology/schema.yaml", "Ontology", "square.grid.3x3"),
    ]

    var body: some View {
        let stats = service.graphStats()
        let present = Set(service.tree.flatMap(\.allFiles).map(\.relativePath))
        let links: [(path: String, label: String, icon: String)] = Self.quickLinks.compactMap { ql in
            if let resolved = service.resolveWikilink(ql.stem), present.contains(resolved) {
                return (resolved, ql.label, ql.icon)
            }
            if present.contains(ql.fallbackPath) {
                return (ql.fallbackPath, ql.label, ql.icon)
            }
            return nil
        }
        let kbGraph = service.fullGraph()

        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Knowledge Base")
                        .font(DS.serif(24, weight: .semibold)).foregroundStyle(DS.Ink.p1)
                    Text("\(stats.notes) notes · \(stats.links) connections")
                        .font(DS.sans(13)).foregroundStyle(DS.Ink.p3)
                }

                if !links.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("QUICK ACCESS").font(DS.sans(10, weight: .semibold)).tracking(0.6)
                            .foregroundStyle(DS.Ink.p4)
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 12)],
                                  alignment: .leading, spacing: 12) {
                            ForEach(links, id: \.path) { ql in
                                Button { onNavigate(ql.path) } label: {
                                    HStack(spacing: 10) {
                                        Image(systemName: ql.icon).font(.system(size: 15))
                                            .foregroundStyle(DS.Accent.ink).frame(width: 20)
                                        Text(ql.label).font(DS.sans(13, weight: .medium))
                                            .foregroundStyle(DS.Ink.p1)
                                        Spacer(minLength: 0)
                                    }
                                    .padding(.horizontal, 14).padding(.vertical, 12)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .editorialCard(padding: 0, neumorphic: true)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plainHit)
                            }
                        }
                    }
                }

                if kbGraph.edges.count > 0 {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("MAP").font(DS.sans(10, weight: .semibold)).tracking(0.6)
                            .foregroundStyle(DS.Ink.p4)
                        KBGraphCanvas(graph: kbGraph, onNavigate: onNavigate,
                                      labelMinDegree: 3, initialScale: 2.0)
                            .frame(height: 460)
                            .frame(maxWidth: 1100)
                            .background(RoundedRectangle(cornerRadius: 8).fill(DS.Paper.sunk.opacity(0.4)))
                            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                        KBGraphLegend(groups: Array(Set(kbGraph.nodes.map(\.group)))
                            .sorted { $0.label < $1.label })
                    }
                }

                Text("Pick a note from the tree, or search above. Click a person, project or `[[link]]` to jump between connected notes.")
                    .font(DS.sans(12)).foregroundStyle(DS.Ink.p4)
                    .frame(maxWidth: 460, alignment: .leading)
            }
            .padding(32)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
