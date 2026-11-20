import SwiftUI

/// What the board and the table show when there's nothing to list: no
/// sessions yet, sessions the default view hides, or filters that match
/// nothing (UI audit #37).
struct SessionsEmptyStateView: View {
    let reason: SessionsEmptyReason
    var onClearFilters: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: reason == .noSessionsYet ? "rectangle.stack" : "line.3.horizontal.decrease.circle")
                .font(DS.symbol(24)).foregroundStyle(DS.Ink.p4)
                .accessibilityHidden(true)
            Text(reason.title)
                .dsFont(.headline, .serif, weight: .medium).foregroundStyle(DS.Ink.p2)
            Text(reason.detail)
                .font(DS.font(.label)).foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 380)
                .fixedSize(horizontal: false, vertical: true)
            if reason.offersClearFilters, let onClearFilters {
                Button("Clear filters", action: onClearFilters)
                    .buttonStyle(.ds(.secondary, size: .small))
                    .padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 24)
    }
}

/// The board (spec §6.2): a Now strip of needs-you and running cards, then one
/// swimlane per project with cards in severity order. Stale and done cards sit
/// behind collapsed "Stale (n)" / "Done (n)" pills beside each lane's title.
///
/// A lane wraps its cards onto as many rows as it needs, each row filled with
/// whole cards (`SessionsLane`). It used to be one sideways-scrolling row,
/// which cut the last card off at every window width (UI audit T4).
struct SessionsBoardView: View {
    let index: SessionIndex
    let filter: SessionsFilter
    let now: Date
    @Binding var selectedID: String?
    /// Clears the search, project and state filters (the empty state's button).
    var onClearFilters: (() -> Void)? = nil

    @State private var expandedStale: Set<String> = []
    @State private var expandedDone: Set<String> = []
    @State private var hoveredParentID: String?

    var body: some View {
        let rows = SessionsLayout.rows(index: index, filter: filter, now: now)
        let strip = SessionsLayout.nowStrip(index: index, filter: filter, now: now)
        let titles = Dictionary(index.sessions.map { ($0.id, $0.displayTitle) }, uniquingKeysWith: { first, _ in first })
        let projectNames = SessionsLayout.projectNames(in: index)
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 22) {
                // Now cards aren't repeated in the lanes below, so each one
                // names its project.
                if !strip.isEmpty {
                    lane(title: "Now", counts: nil, chips: { EmptyView() }) {
                        grid {
                            ForEach(strip) {
                                card($0, titles: titles,
                                     projectName: projectNames[$0.projectKey] ?? SessionsLayout.basename($0.projectKey))
                            }
                        }
                    }
                }
                ForEach(rows) { row in
                    lane(title: row.name, counts: row.counts, chips: {
                        toggle("Stale", count: row.stale.count, rowID: row.id, expanded: $expandedStale)
                        toggle("Done", count: row.done.count, rowID: row.id, expanded: $expandedDone)
                    }) {
                        if !row.active.isEmpty {
                            grid { ForEach(row.active) { card($0, titles: titles) } }
                        }
                        // Opened stale or done cards start under a quiet rule, so they
                        // don't read as more of the lane's active work (T4 review).
                        if expandedStale.contains(row.id), !row.stale.isEmpty {
                            groupRule("Stale")
                            grid { ForEach(row.stale) { card($0, titles: titles) } }
                        }
                        if expandedDone.contains(row.id), !row.done.isEmpty {
                            groupRule("Done")
                            grid { ForEach(row.done) { card($0, titles: titles) } }
                        }
                    }
                }
                if rows.isEmpty && strip.isEmpty,
                   let reason = SessionsLayout.emptyReason(index: index, filter: filter, now: now) {
                    SessionsEmptyStateView(reason: reason, onClearFilters: onClearFilters)
                        .padding(.top, 40)
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 16)
        }
    }

    private func lane<Chips: View, Content: View>(
        title: String, counts: [AgentSessionState: Int]?,
        @ViewBuilder chips: () -> Chips, @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                Text(title)
                    .dsFont(.title, .serif, weight: .medium)
                    .foregroundStyle(DS.Ink.p1)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(title)
                if let counts {
                    countPills(counts)
                }
                chips()
            }
            content()
        }
    }

    /// Whole cards, as many to a row as fit (`SessionsLane`).
    private func grid<Cards: View>(@ViewBuilder _ cards: () -> Cards) -> some View {
        LazyVGrid(columns: SessionsLane.columns, alignment: .leading, spacing: SessionsLane.spacing) { cards() }
            .padding(.vertical, 2)
    }

    /// A quiet labelled rule above a lane's opened stale or done cards.
    private func groupRule(_ label: String) -> some View {
        HStack(spacing: 8) {
            Text(label.uppercased())
                .font(DS.font(.caption2, weight: .medium))
                .tracking(1)
                .foregroundStyle(DS.Ink.p4)
            EditorialRule(color: DS.Rule.soft)
        }
        .padding(.top, 4)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    /// One tiny pill per state present in the lane, in severity order (spec §6.2).
    private func countPills(_ counts: [AgentSessionState: Int]) -> some View {
        HStack(spacing: 4) {
            ForEach(AgentSessionState.allCases.filter { counts[$0] != nil }, id: \.self) { state in
                DSPill("\(counts[state] ?? 0)", dot: SessionStateStyle.color(state))
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(counts[state] ?? 0) \(state.label.lowercased())")
            }
        }
    }

    private func card(_ session: AgentSession, titles: [String: String], projectName: String? = nil) -> some View {
        Button {
            selectedID = session.id
        } label: {
            SessionCardView(
                session: session,
                now: now,
                projectName: projectName,
                parentTitle: session.parentSessionID.flatMap { titles[$0] },
                isSelected: selectedID == session.id,
                isHighlighted: hoveredParentID == session.id
            )
        }
        .buttonStyle(.dsCard())
        .onHover { inside in
            if inside { hoveredParentID = session.parentSessionID } else if hoveredParentID == session.parentSessionID { hoveredParentID = nil }
        }
    }

    /// Shows or hides a lane's stale or done cards, which then wrap in after its active ones.
    @ViewBuilder
    private func toggle(_ label: String, count: Int, rowID: String, expanded: Binding<Set<String>>) -> some View {
        if count > 0 {
            let isOpen = expanded.wrappedValue.contains(rowID)
            Button {
                if isOpen { expanded.wrappedValue.remove(rowID) } else { expanded.wrappedValue.insert(rowID) }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: isOpen ? "chevron.down" : "chevron.right")
                        .imageScale(.small)
                    Text("\(label) (\(count))")
                }
            }
            .buttonStyle(.dsChip(selected: isOpen))
            .fixedSize()
            .accessibilityLabel(isOpen ? "Hide \(label.lowercased()) sessions" : "Show \(count) \(label.lowercased()) sessions")
        }
    }
}

/// How a board lane lays out its cards (UI audit T4): as many whole cards per
/// row as fit, at least `minCardWidth` each, stretched to fill the row, then
/// the next row. Nothing is cut off at the lane's edge.
enum SessionsLane {
    static let minCardWidth: CGFloat = 240
    static let spacing: CGFloat = 10
    static let columns = [GridItem(.adaptive(minimum: minCardWidth), spacing: spacing, alignment: .top)]
}
