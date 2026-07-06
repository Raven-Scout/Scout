import SwiftUI

/// Right pane for the selected note: a Links/Graph toggle. Links lists outgoing
/// `[[wikilinks]]` and backlinks (both navigable); Graph shows the local
/// neighbourhood. Both read from `KnowledgeBaseService`'s wikilink index.
struct KBRightPanel: View {
    let relPath: String
    @ObservedObject var service: KnowledgeBaseService
    let onNavigate: (String) -> Void

    private enum Tab: String { case links, graph }
    @State private var tab: Tab = .links

    var body: some View {
        VStack(spacing: 0) {
            EditorialSegmentedControl(
                selection: $tab,
                options: [(label: "Links", value: .links), (label: "Graph", value: .graph)],
                minSegmentWidth: 70
            )
            .padding(12)
            EditorialRule()

            switch tab {
            case .links:
                ScrollView { linksContent.padding(12) }
            case .graph:
                KBLocalGraphView(graph: service.localGraph(around: relPath),
                                 onNavigate: onNavigate)
                    .padding(.bottom, 8)
                    // Rebuild (fresh simulation, re-centered) whenever the open
                    // note changes, so the graph always reflects the current page.
                    .id(relPath)
            }
        }
        .frame(maxHeight: .infinity)
        .background(DS.Paper.sunk.opacity(0.35))
    }

    private var linksContent: some View {
        let outgoing = service.outgoingLinks(for: relPath)
        let backlinks = service.backlinks(for: relPath)
        return VStack(alignment: .leading, spacing: 18) {
            section(title: "Links from this note", count: outgoing.count) {
                if outgoing.isEmpty {
                    emptyLine("No outgoing links")
                } else {
                    FlowLayout(spacing: 6) {
                        ForEach(outgoing) { link in
                            Button { if let r = link.resolved { onNavigate(r) } } label: {
                                Text(link.target)
                                    .font(DS.sans(11))
                                    .foregroundStyle(link.resolved == nil ? DS.Ink.p4 : DS.Accent.ink)
                                    .padding(.horizontal, 8).padding(.vertical, 3)
                                    .background(EditorialChipBackground())
                                    .opacity(link.resolved == nil ? 0.6 : 1)
                            }
                            .buttonStyle(.plain)
                            .disabled(link.resolved == nil)
                            .help(link.resolved == nil ? "Note not found" : link.resolved!)
                        }
                    }
                }
            }

            section(title: "Linked from", count: backlinks.count) {
                if backlinks.isEmpty {
                    emptyLine("No notes link here")
                } else {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(backlinks) { bl in
                            Button { onNavigate(bl.path) } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(bl.name).font(DS.sans(12, weight: .medium)).foregroundStyle(DS.Ink.p1)
                                    if !bl.excerpt.isEmpty {
                                        InlineMarkdownText(bl.excerpt)
                                            .font(DS.sans(10.5)).foregroundStyle(DS.Ink.p3)
                                            .lineLimit(2).multilineTextAlignment(.leading)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func section<Content: View>(title: String, count: Int, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text(title.uppercased()).font(DS.sans(10, weight: .semibold)).tracking(0.5)
                    .foregroundStyle(DS.Ink.p4)
                Text("\(count)").font(DS.mono(10)).foregroundStyle(DS.Ink.p4)
                Spacer(minLength: 0)
            }
            content()
        }
    }

    private func emptyLine(_ text: String) -> some View {
        Text(text).font(DS.sans(11)).foregroundStyle(DS.Ink.p4)
    }
}
