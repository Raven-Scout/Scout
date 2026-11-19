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
            sectionLabel("KNOWLEDGE BASE HEALTH")
            danglingRow
            row(KBNetworkStats.healthTitle(stats.orphans.count,
                                           singular: "orphaned note", plural: "orphaned notes"),
                icon: "circle.dashed", metric: .orphans,
                labels: stats.orphans.map(KBNode.displayName(forPath:)), paths: stats.orphans)
            islandsRow
            row(KBNetworkStats.healthTitle(stats.weaklyLinked.count,
                                           singular: "weakly linked note", plural: "weakly linked notes"),
                icon: "link", metric: .weaklyLinked,
                labels: stats.weaklyLinked.map(KBNode.displayName(forPath:)), paths: stats.weaklyLinked)
            setAsideRow
        }
    }

    /// Dangling links, one line per note that holds them (UI audit #73): the
    /// note opens on click, its missing targets follow. Notes with the most
    /// broken links first.
    private var danglingRow: some View {
        let groups = stats.danglingBySource
        return VStack(alignment: .leading, spacing: 6) {
            rowHeader(stats.danglingTitle, icon: "link.badge.plus", clean: groups.isEmpty, metric: .dangling)
            ForEach(groups.prefix(topN)) { danglingLine($0) }
            if groups.count > topN {
                let window = KBNetworkStats.disclosureWindow(count: groups.count, topN: topN, cap: expandCap)
                DisclosureGroup("Show all \(groups.count) notes") {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(groups[window.shown]) { danglingLine($0) }
                        moreLine(window.hidden)
                    }
                    .padding(.top, 4)
                }
                .font(DS.font(.caption)).foregroundStyle(DS.Accent.ink)
            }
        }
    }

    /// Most targets listed after a note before "+N more".
    private let targetsShown = 4

    private func danglingLine(_ group: KBDanglingGroup) -> some View {
        let name = KBNode.displayName(forPath: group.source)
        let shown = group.targets.prefix(targetsShown).joined(separator: ", ")
        let rest = group.targets.count - min(group.targets.count, targetsShown)
        let targets = "→ " + shown + (rest > 0 ? " +\(rest) more" : "")
        return HStack(alignment: .firstTextBaseline, spacing: 8) {
            chips(["\(name) · \(group.targets.count)"], [group.source])
                .fixedSize()
            Text(targets)
                .font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
                .lineLimit(1).truncationMode(.tail)
                .help(group.targets.joined(separator: ", "))
        }
    }

    /// Archive copies and paperwork aren't live notes, so they're left out of
    /// the rows above and the insight below; this line says how many.
    @ViewBuilder
    private var setAsideRow: some View {
        if let summary = stats.setAsideSummary {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Image(systemName: "archivebox").font(DS.font(.label)).foregroundStyle(DS.Ink.p3)
                        .accessibilityHidden(true)
                    Text(summary).font(DS.font(.label, weight: .medium)).foregroundStyle(DS.Ink.p2)
                }
                Text("Notes in archive folders, and paperwork such as receipts, invoices and certificates, aren't checked for links.")
                    .font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
        }
    }

    /// A health row: an ok/warn icon + title, then the items as chips.
    private func row(_ title: String, icon: String, metric: KBHealthMetric,
                     labels: [String], paths: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            rowHeader(title, icon: icon, clean: labels.isEmpty, metric: metric)
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
                      icon: "square.on.square.dashed", clean: stats.islands.isEmpty, metric: .islands)
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
                .font(DS.font(.caption)).foregroundStyle(DS.Accent.ink)
            }
        }
    }

    // MARK: - Insight

    private var insightBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionLabel("INSIGHT")
            insightLine(stats.degreeSummary, metric: .connections)
            insightLine(stats.componentsSummary, metric: .clusters)

            if !stats.topHubs.isEmpty {
                Text("Top hubs").font(DS.font(.caption, weight: .semibold)).foregroundStyle(DS.Ink.p3)
                explanation(.hubs)
                chipList(stats.topHubs.map { "\(KBNode.displayName(forPath: $0.path)) · \($0.degree)" },
                         stats.topHubs.map(\.path))
            }

            Text("By type").font(DS.font(.caption, weight: .semibold)).foregroundStyle(DS.Ink.p3)
            FlowLayout(spacing: 10) {
                ForEach(stats.byType.filter { $0.count > 0 }) { tc in
                    HStack(spacing: 4) {
                        Circle().fill(tc.group.color).frame(width: 7, height: 7)
                        Text("\(tc.group.label) \(tc.count)").font(DS.font(.caption)).foregroundStyle(DS.Ink.p2)
                    }
                }
            }
        }
    }

    // MARK: - Building blocks

    private func sectionLabel(_ text: String) -> some View {
        Text(text).font(DS.font(.caption2, weight: .semibold)).tracking(0.6).foregroundStyle(DS.Ink.p4)
    }

    /// A row's icon and title, with the metric's one-line explanation under it.
    private func rowHeader(_ title: String, icon: String, clean: Bool, metric: KBHealthMetric) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Image(systemName: icon).font(DS.font(.label))
                    .foregroundStyle(clean ? DS.Status.ok : DS.Status.warn)
                    .accessibilityHidden(true)
                Text(title).font(DS.font(.label, weight: .medium)).foregroundStyle(DS.Ink.p2)
            }
            explanation(metric).padding(.leading, 18)
        }
    }

    private func insightLine(_ text: String, metric: KBHealthMetric) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(text).font(DS.font(.label)).foregroundStyle(DS.Ink.p2)
            explanation(metric)
        }
    }

    private func explanation(_ metric: KBHealthMetric) -> some View {
        Text(metric.explanation)
            .font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
            .fixedSize(horizontal: false, vertical: true)
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
            .font(DS.font(.caption)).foregroundStyle(DS.Accent.ink)
        }
    }

    @ViewBuilder
    private func moreLine(_ hidden: Int) -> some View {
        if hidden > 0 {
            Text("…and \(hidden) more").font(DS.font(.caption)).foregroundStyle(DS.Ink.p4).padding(.top, 2)
        }
    }

    private func chips(_ labels: [String], _ paths: [String]) -> some View {
        FlowLayout(spacing: 6) {
            ForEach(Array(zip(labels, paths).enumerated()), id: \.offset) { _, pair in
                Button { onOpen(pair.1) } label: {
                    Text(pair.0).font(DS.font(.caption)).foregroundStyle(DS.Ink.p1)
                        .lineLimit(1).truncationMode(.middle)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Capsule().fill(DS.Paper.sunk))
                }
                .buttonStyle(.plainHit)
                .help(pair.0)
            }
        }
    }
}
