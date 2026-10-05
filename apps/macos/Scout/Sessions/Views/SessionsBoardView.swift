import SwiftUI

/// The board (spec §6.2): a Now strip of needs-you and running cards, then one
/// swimlane per project with cards in severity order. Stale and done cards sit
/// behind collapsed "Stale (n)" / "Done (n)" pills at the end of each lane.
struct SessionsBoardView: View {
    let index: SessionIndex
    let filter: SessionsFilter
    let now: Date
    @Binding var selectedID: String?

    @State private var expandedStale: Set<String> = []
    @State private var expandedDone: Set<String> = []
    @State private var hoveredParentID: String?

    var body: some View {
        let rows = SessionsLayout.rows(index: index, filter: filter, now: now)
        let strip = SessionsLayout.nowStrip(index: index, filter: filter, now: now)
        let titles = Dictionary(index.sessions.map { ($0.id, $0.displayTitle) }, uniquingKeysWith: { first, _ in first })
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 22) {
                if !strip.isEmpty {
                    lane(title: "Now", counts: nil) {
                        ForEach(strip) { card($0, titles: titles) }
                    }
                }
                ForEach(rows) { row in
                    lane(title: row.name, counts: row.counts) {
                        ForEach(row.active) { card($0, titles: titles) }
                        collapsible("Stale", sessions: row.stale, rowID: row.id, expanded: $expandedStale, titles: titles)
                        collapsible("Done", sessions: row.done, rowID: row.id, expanded: $expandedDone, titles: titles)
                    }
                }
                if rows.isEmpty && strip.isEmpty {
                    Text("No sessions match these filters.")
                        .font(DS.sans(13))
                        .foregroundStyle(DS.Ink.p3)
                        .padding(.top, 40)
                        .frame(maxWidth: .infinity)
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
                if let counts {
                    countPills(counts)
                }
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 10) { cards() }
                    .padding(.vertical, 2)
            }
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

    private func card(_ session: AgentSession, titles: [String: String]) -> some View {
        Button {
            selectedID = session.id
        } label: {
            SessionCardView(
                session: session,
                now: now,
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
