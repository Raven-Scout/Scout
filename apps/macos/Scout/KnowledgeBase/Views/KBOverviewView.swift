import SwiftUI

/// Landing page shown when no note is selected. The knowledge map fills the
/// page — the whole vault, searchable, filterable by type — under a slim header
/// with the vault's size, quick links to the hub notes, and a Map | Health
/// switch. Health is the vault-health and connectivity report that used to
/// sit above the map.
struct KBOverviewView: View {
    @ObservedObject var service: KnowledgeBaseService
    let onNavigate: (String) -> Void
    /// A note to select on the map (from a note's "Show on map").
    var mapFocus: String? = nil

    enum Tab: String { case map, health }
    @AppStorage("kbOverviewTab") private var tab: Tab = .map

    /// Canonical hub notes, in display order. Each resolves by its note stem
    /// through the wikilink index (so a moved hub still gets a link), with an
    /// exact-path fallback for non-markdown files the index doesn't cover.
    /// Hubs that resolve to nothing on disk are hidden.
    static let quickLinks: [(stem: String, fallbackPath: String, label: String, icon: String)] = [
        ("knowledge-base",  "knowledge-base/knowledge-base.md",    "Index",    "book.closed"),
        ("people",          "knowledge-base/people.md",            "People",   "person.2"),
        ("projects",        "knowledge-base/projects/projects.md", "Projects", "folder"),
        ("issues",          "knowledge-base/issues.md",            "Issues",   "exclamationmark.triangle"),
        ("channels",        "knowledge-base/channels.md",          "Channels", "number"),
        ("research-queue",  "knowledge-base/research-queue.md",    "Research", "magnifyingglass"),
        ("review-queue",    "knowledge-base/review-queue.md",      "Review",   "checkmark.circle"),
        ("schema",          "knowledge-base/ontology/schema.yaml", "Ontology", "square.grid.3x3"),
    ]

    private func presentQuickLinks() -> [(path: String, label: String, icon: String)] {
        let present = Set(service.tree.flatMap(\.allFiles).map(\.relativePath))
        return Self.quickLinks.compactMap { ql in
            if let resolved = service.resolveWikilink(ql.stem), present.contains(resolved) {
                return (resolved, ql.label, ql.icon)
            }
            if present.contains(ql.fallbackPath) { return (ql.fallbackPath, ql.label, ql.icon) }
            return nil
        }
    }

    var body: some View {
        let net = service.networkStats()
        let loading = KnowledgeBaseView.showsLoading(service.state, treeIsEmpty: service.tree.isEmpty)
        VStack(spacing: 0) {
            header(net, loading: loading)
            EditorialRule()
            if loading {
                // Not "0 notes" or the map's "No notes yet" while the first
                // scan runs (UI audit #76).
                KnowledgeBaseView.LoadingState()
            } else {
                content(net)
            }
        }
        .onChange(of: mapFocus) { _, new in if new != nil { tab = .map } }
    }

    @ViewBuilder
    private func content(_ net: KBNetworkStats) -> some View {
        switch tab {
        case .map:
            KBMapWebView(service: service, mode: .vault, focus: mapFocus, onOpen: onNavigate)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .health:
            ScrollView {
                KBStatsView(stats: net, onOpen: onNavigate)
                    .padding(28)
                    .frame(maxWidth: 820, alignment: .leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func header(_ net: KBNetworkStats, loading: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Knowledge Base")
                        .font(DS.font(.hero, .serif, weight: .semibold)).foregroundStyle(DS.Ink.p1)
                        .glossaryHelp(.knowledgeBase)
                    Text(loading ? "Loading notes…"
                         : "\(net.noteCount.formatted()) notes · \(net.linkCount.formatted()) links\(healthNote(net))")
                        .font(DS.font(.label)).foregroundStyle(DS.Ink.p3)
                }
                Spacer(minLength: 12)
                EditorialSegmentedControl(
                    selection: $tab,
                    options: [(label: "Map", value: .map), (label: "Health", value: .health)],
                    minSegmentWidth: 64
                )
            }
            let links = presentQuickLinks()
            if !links.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(links, id: \.path) { ql in
                            Button { onNavigate(ql.path) } label: {
                                HStack(spacing: 5) {
                                    Image(systemName: ql.icon).font(DS.font(.caption2))
                                        .foregroundStyle(DS.Accent.ink)
                                    Text(ql.label).font(DS.font(.caption, weight: .medium))
                                        .foregroundStyle(DS.Ink.p1)
                                }
                                .padding(.horizontal, 10).padding(.vertical, 4)
                                .background(EditorialChipBackground())
                                .contentShape(Capsule())
                            }
                            .buttonStyle(.plainHit) // ds-guard: hit area (a link chip; only its icon is accent)
                            .help("Open \(ql.label)")
                        }
                    }
                }
            }
        }
        .padding(.horizontal, 20).padding(.top, 14).padding(.bottom, 12)
    }

    /// " · 3 things to tidy" when the Health tab has something to show.
    private func healthNote(_ net: KBNetworkStats) -> String {
        let issues = net.dangling.count + net.islands.count
        guard issues > 0 else { return "" }
        return " · \(issues) to tidy in Health"
    }
}
