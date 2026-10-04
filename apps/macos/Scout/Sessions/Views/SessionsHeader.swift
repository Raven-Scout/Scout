import SwiftUI

/// Board or table. Persists across launches via `@SceneStorage("sessionsView")`.
enum SessionsViewMode: String, CaseIterable, Hashable {
    case board
    case table

    var label: String {
        switch self {
        case .board: return "Board"
        case .table: return "Table"
        }
    }
}

/// Title, freshness line, search, project and option menus, the Board/Table
/// toggle, then one chip per state with live counts (spec §6.1).
struct SessionsHeader: View {
    @Binding var viewMode: SessionsViewMode
    @Binding var filter: SessionsFilter
    /// From `SessionsLayout.stateCounts` — every filter but the chips.
    let counts: [AgentSessionState: Int]
    let projects: [SessionProject]
    /// Read inside a `TimelineView`: `lastRefreshAt` is deliberately not
    /// published, so the line ticks without re-rendering the page.
    let service: SessionIndexService

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Sessions")
                        .font(DS.serif(28, weight: .medium))
                        .foregroundStyle(DS.Ink.p1)
                    TimelineView(.periodic(from: .now, by: 10)) { context in
                        Text(freshness(now: context.date))
                            .font(DS.sans(12))
                            .foregroundStyle(DS.Ink.p3)
                    }
                }
                Spacer()
                TextField("Search sessions", text: $filter.search)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 190)
                projectMenu
                optionsMenu
                EditorialSegmentedControl(
                    selection: $viewMode,
                    options: SessionsViewMode.allCases.map { ($0.label, $0) }
                )
            }
            stateChips
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
    }

    private func freshness(now: Date) -> String {
        var parts: [String] = []
        if let refreshed = service.lastRefreshAt {
            parts.append("updated \(SessionsFormat.ago(refreshed, now: now))")
        }
        if let pr = service.prStatus {
            parts.append(pr.errors.isEmpty
                ? "PRs checked \(SessionsFormat.ago(pr.finishedAt, now: now))"
                : "PR check: \(pr.errors.count) problem\(pr.errors.count == 1 ? "" : "s")")
        }
        return parts.isEmpty ? "Reading your Claude Code sessions…" : parts.joined(separator: " · ")
    }

    private var projectMenu: some View {
        Menu {
            Button("All projects") { filter.projectKey = nil }
            Divider()
            ForEach(projects, id: \.key) { project in
                Button(project.name) { filter.projectKey = project.key }
            }
        } label: {
            Text(projects.first { $0.key == filter.projectKey }?.name ?? "All projects")
                .font(DS.sans(12.5))
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
    }

    private var optionsMenu: some View {
        Menu {
            Toggle("Show Scout's own runs", isOn: $filter.showScoutRuns)
            Toggle("Show recently done", isOn: $filter.showRecentlyDone)
        } label: {
            Image(systemName: "line.3.horizontal.decrease.circle")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .accessibilityLabel("View options")
    }

    private var stateChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                chip(label: "All", count: counts.values.reduce(0, +), state: nil, isSelected: filter.states.isEmpty) {
                    filter.states = []
                }
                ForEach(AgentSessionState.allCases, id: \.self) { state in
                    if state != .done || filter.showRecentlyDone {
                        chip(label: state.label, count: counts[state] ?? 0, state: state,
                             isSelected: filter.states.contains(state)) {
                            if filter.states.contains(state) { filter.states.remove(state) } else { filter.states.insert(state) }
                        }
                    }
                }
            }
        }
    }

    private func chip(
        label: String, count: Int, state: AgentSessionState?, isSelected: Bool, action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let state { SessionStateDot(state: state) }
                Text(label).font(DS.sans(12, weight: .medium))
                Text("\(count)")
                    .font(DS.mono(11))
                    .foregroundStyle(isSelected ? DS.Paper.base.opacity(0.85) : DS.Ink.p3)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Capsule().fill(isSelected ? DS.Ink.p1 : DS.Paper.raised))
            .foregroundStyle(isSelected ? DS.Paper.base : DS.Ink.p2)
        }
        .buttonStyle(.plainHit)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}
