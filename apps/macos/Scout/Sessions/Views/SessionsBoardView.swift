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
                .font(DS.sans(24)).foregroundStyle(DS.Ink.p4)
                .accessibilityHidden(true)
            Text(reason.title)
                .font(DS.serif(15, weight: .medium)).foregroundStyle(DS.Ink.p2)
            Text(reason.detail)
                .font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
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
/// behind collapsed "Stale (n)" / "Done (n)" pills at the end of each lane.
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
                    lane(title: "Now", counts: nil) {
                        ForEach(strip) {
                            card($0, titles: titles,
                                 projectName: projectNames[$0.projectKey] ?? SessionsLayout.basename($0.projectKey))
                        }
                    }
                }
                ForEach(rows) { row in
                    lane(title: row.name, counts: row.counts) {
                        ForEach(row.active) { card($0, titles: titles) }
                        collapsible("Stale", sessions: row.stale, rowID: row.id, expanded: $expandedStale, titles: titles)
                        collapsible("Done", sessions: row.done, rowID: row.id, expanded: $expandedDone, titles: titles)
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

    private func lane<Cards: View>(
        title: String, counts: [AgentSessionState: Int]?, @ViewBuilder cards: () -> Cards
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                Text(title)
                    .font(DS.serif(17, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help(title)
                if let counts {
                    countPills(counts)
                }
            }
            // Indicators on: a lane wider than the page cut its last card off
            // with no hint that it scrolls (UI audit C6).
            ScrollView(.horizontal) {
                HStack(alignment: .top, spacing: 10) { cards() }
                    .padding(.vertical, 2)
                    .padding(.bottom, 6)
            }
            .scrollIndicators(.visible)
        }
    }

    /// One tiny pill per state present in the lane, in severity order (spec §6.2).
    private func countPills(_ counts: [AgentSessionState: Int]) -> some View {
        HStack(spacing: 4) {
            ForEach(AgentSessionState.allCases.filter { counts[$0] != nil }, id: \.self) { state in
                HStack(spacing: 3) {
                    SessionStateDot(state: state)
                    Text("\(counts[state] ?? 0)")
                        .font(DS.mono(10.5))
                        .foregroundStyle(DS.Ink.p3)
                }
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(Capsule().fill(DS.Paper.sunk))
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
        .buttonStyle(.plainHit)
        .onHover { inside in
            if inside { hoveredParentID = session.parentSessionID } else if hoveredParentID == session.parentSessionID { hoveredParentID = nil }
        }
    }

    @ViewBuilder
    private func collapsible(
        _ label: String, sessions: [AgentSession], rowID: String,
        expanded: Binding<Set<String>>, titles: [String: String]
    ) -> some View {
        if !sessions.isEmpty {
            let isOpen = expanded.wrappedValue.contains(rowID)
            Button {
                if isOpen { expanded.wrappedValue.remove(rowID) } else { expanded.wrappedValue.insert(rowID) }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: isOpen ? "chevron.left" : "chevron.right")
                        .imageScale(.small)
                    Text("\(label) (\(sessions.count))")
                }
                .font(DS.sans(11.5, weight: .medium))
                .foregroundStyle(DS.Ink.p3)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Capsule().fill(DS.Paper.sunk))
            }
            .buttonStyle(.plainHit)
            .accessibilityLabel(isOpen ? "Hide \(label.lowercased()) sessions" : "Show \(sessions.count) \(label.lowercased()) sessions")
            if isOpen {
                ForEach(sessions) { card($0, titles: titles) }
            }
        }
    }
}
