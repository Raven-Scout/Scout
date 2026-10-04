import SwiftUI

/// The overview's network section: vault-health problem lists (clickable) plus
/// read-only connectivity insight, rendered from one `KBNetworkStats` value.
struct KBStatsView: View {
    let stats: KBNetworkStats
    /// Open a note in the editor.
    let onOpen: (String) -> Void

    private let topN = 5
    /// Most items a "Show all" disclosure renders; the rest become a count.
    private let expandCap = 200

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            healthBlock
            insightBlock
        }
    }

    // MARK: - Health

    private var healthBlock: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionLabel("VAULT HEALTH")
            row(KBNetworkStats.healthTitle(stats.dangling.count,
                                           singular: "dangling link", plural: "dangling links"),
                icon: "link.badge.plus",
                labels: stats.dangling.map { "\(KBNode.displayName(forPath: $0.source)) → \($0.target)" },
                paths: stats.dangling.map(\.source))
            row(KBNetworkStats.healthTitle(stats.orphans.count,
                                           singular: "orphaned note", plural: "orphaned notes"),
                icon: "circle.dashed",
                labels: stats.orphans.map(KBNode.displayName(forPath:)), paths: stats.orphans)
            islandsRow
            row(KBNetworkStats.healthTitle(stats.weaklyLinked.count,
                                           singular: "weakly linked note", plural: "weakly linked notes"),
                icon: "link",
                labels: stats.weaklyLinked.map(KBNode.displayName(forPath:)), paths: stats.weaklyLinked)
        }
    }

    /// A health row: an ok/warn icon + title, then the items as chips.
    private func row(_ title: String, icon: String, labels: [String], paths: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            rowHeader(title, icon: icon, clean: labels.isEmpty)
            if !labels.isEmpty { chipList(labels, paths) }
        }
    }

    /// Islands are lists of lists: one chip row per island, top-N islands
    /// shown, the rest behind a disclosure.
    private var islandsRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            rowHeader(KBNetworkStats.healthTitle(stats.islands.count,
                                                 singular: "disconnected island",
                                                 plural: "disconnected islands"),
                      icon: "square.on.square.dashed", clean: stats.islands.isEmpty)
            ForEach(Array(stats.islands.prefix(topN).enumerated()), id: \.offset) { _, island in
                chips(island.map(KBNode.displayName(forPath:)), island)
            }
            if stats.islands.count > topN {
                let window = KBNetworkStats.disclosureWindow(count: stats.islands.count, topN: topN, cap: expandCap)
                DisclosureGroup("Show all \(stats.islands.count)") {
                    ForEach(Array(stats.islands[window.shown].enumerated()), id: \.offset) { _, island in
                        chips(island.map(KBNode.displayName(forPath:)), island)
                    }
                    .padding(.top, 4)
                    moreLine(window.hidden)
                }
                .font(DS.sans(11)).foregroundStyle(DS.Accent.ink)
            }
        }
    }

    // MARK: - Insight

    private var insightBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionLabel("INSIGHT")
            Text(stats.degreeSummary).font(DS.sans(12)).foregroundStyle(DS.Ink.p2)
            Text(stats.componentsSummary).font(DS.sans(12)).foregroundStyle(DS.Ink.p2)

            if !stats.topHubs.isEmpty {
                Text("Top hubs").font(DS.sans(11, weight: .semibold)).foregroundStyle(DS.Ink.p3)
                chipList(stats.topHubs.map { "\(KBNode.displayName(forPath: $0.path)) · \($0.degree)" },
                         stats.topHubs.map(\.path))
            }

            Text("By type").font(DS.sans(11, weight: .semibold)).foregroundStyle(DS.Ink.p3)
            FlowLayout(spacing: 10) {
                ForEach(stats.byType.filter { $0.count > 0 }) { tc in
                    HStack(spacing: 4) {
                        Circle().fill(tc.group.color).frame(width: 7, height: 7)
                        Text("\(tc.group.label) \(tc.count)").font(DS.sans(11)).foregroundStyle(DS.Ink.p2)
                    }
                }
            }
        }
    }

    // MARK: - Building blocks

    private func sectionLabel(_ text: String) -> some View {
        Text(text).font(DS.sans(10, weight: .semibold)).tracking(0.6).foregroundStyle(DS.Ink.p4)
    }

    private func rowHeader(_ title: String, icon: String, clean: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 12))
                .foregroundStyle(clean ? DS.Status.ok : DS.Status.warn)
            Text(title).font(DS.sans(12, weight: .medium)).foregroundStyle(DS.Ink.p2)
        }
    }

    /// The first `topN` items as chips, plus a "Show all N" disclosure for the
    /// rest (capped at `expandCap`). `labels[i]` is shown; `paths[i]` is opened.
    @ViewBuilder
    private func chipList(_ labels: [String], _ paths: [String]) -> some View {
        chips(Array(labels.prefix(topN)), Array(paths.prefix(topN)))
        if labels.count > topN {
            let window = KBNetworkStats.disclosureWindow(count: labels.count, topN: topN, cap: expandCap)
            DisclosureGroup("Show all \(labels.count)") {
                chips(Array(labels[window.shown]), Array(paths[window.shown]))
                    .padding(.top, 4)
                moreLine(window.hidden)
            }
            .font(DS.sans(11)).foregroundStyle(DS.Accent.ink)
        }
    }

    @ViewBuilder
    private func moreLine(_ hidden: Int) -> some View {
        if hidden > 0 {
            Text("…and \(hidden) more").font(DS.sans(11)).foregroundStyle(DS.Ink.p4).padding(.top, 2)
        }
    }

    private func chips(_ labels: [String], _ paths: [String]) -> some View {
        FlowLayout(spacing: 6) {
            ForEach(Array(zip(labels, paths).enumerated()), id: \.offset) { _, pair in
                Button { onOpen(pair.1) } label: {
                    Text(pair.0).font(DS.sans(11)).foregroundStyle(DS.Ink.p1)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Capsule().fill(DS.Paper.sunk))
                }
                .buttonStyle(.plainHit)
            }
        }
    }
}
