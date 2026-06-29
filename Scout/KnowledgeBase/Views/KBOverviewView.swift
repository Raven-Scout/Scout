import SwiftUI

/// Landing page shown when no note is selected: KB stats plus quick-access tiles
/// to the canonical hub notes (only those that actually exist).
struct KBOverviewView: View {
    @ObservedObject var service: KnowledgeBaseService
    let onNavigate: (String) -> Void

    /// Canonical hub notes, in display order. Filtered to those present on disk.
    private static let quickLinks: [(path: String, label: String, icon: String)] = [
        ("knowledge-base/knowledge-base.md", "Index",        "book.closed"),
        ("knowledge-base/people.md",         "People",       "person.2"),
        ("knowledge-base/issues.md",         "Issues",       "exclamationmark.triangle"),
        ("knowledge-base/projects/projects.md", "Projects",  "folder"),
        ("knowledge-base/channels.md",       "Channels",     "number"),
        ("knowledge-base/research-queue.md", "Research",     "magnifyingglass"),
        ("knowledge-base/review-queue.md",   "Review",       "checkmark.circle"),
        ("knowledge-base/ontology/schema.yaml", "Ontology",  "square.grid.3x3"),
    ]

    var body: some View {
        let stats = service.graphStats()
        let present = Set(service.tree.flatMap(\.allFiles).map(\.relativePath))
        let links = Self.quickLinks.filter { present.contains($0.path) }

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

                Text("Pick a note from the tree, or search above. Click a person, project or `[[link]]` to jump between connected notes.")
                    .font(DS.sans(12)).foregroundStyle(DS.Ink.p4)
                    .frame(maxWidth: 460, alignment: .leading)
            }
            .padding(32)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
