import SwiftUI

/// The run list, headed "Runs" (card T2: "Sessions" is the Agent Sessions
/// page's word) — editorial card with search + type filter and a flat row
/// layout (status icon · name/when · status).
///
/// CC-8: previously used `NavigationLink` to push detail into a stack,
/// which buried the sessions list. Now exposes a tap callback so the
/// parent (ControlCenterView) can open the detail as a side panel — and
/// surfaces selected-row highlight + a hover chevron so the row is
/// obviously clickable.
struct SessionsListView: View {
    @EnvironmentObject var state: AppState
    var dayFilter: Date?
    let onSelect: (Run) -> Void
    let selectedRunID: Run.ID?

    @State private var typeFilter: Set<RunType> = Set(RunType.allCases)
    @State private var search: String = ""
    @State private var window = RunWindow()

    var filtered: [Run] {
        // The filtered day's bounds once, not a calendar call per run per render.
        let day = dayFilter.flatMap { Calendar.current.dateInterval(of: .day, for: $0) }
        let scheduleTypes = Self.scheduleTypes(
            slots: state.scheduleEditService.slots, upcoming: state.scheduleService.upcoming)
        return state.sessionLogService.runs.filter { run in
            if let day, !(day.start..<day.end).contains(run.startedAt) { return false }
            if !typeFilter.contains(run.listType(scheduleTypes: scheduleTypes)) { return false }
            if !search.isEmpty {
                if !Self.matches(run, search) { return false }
            }
            return true
        }
    }

    /// Each schedule's run type by slot key, from its `slot_type`, for
    /// `Run.listType`: a schedule the owner made (`overnight-research`) has
    /// no default key to map. The full schedule (`scoutctl schedule list`)
    /// loads with the Schedules page; the upcoming list (polled from launch)
    /// carries every schedule that fires in the next eight days, so it covers
    /// the Control Center before that page opens. The full schedule wins.
    static func scheduleTypes(slots: [Slot], upcoming: [UpcomingRun]) -> [String: RunType] {
        var out: [String: RunType] = [:]
        for up in upcoming { out[up.slotKey] = up.type }
        for slot in slots {
            if let type = RunType(slotKey: slot.key, slotType: slot.type.rawValue) { out[slot.key] = type }
        }
        return out
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            let runs = filtered
            if runs.isEmpty {
                emptyState
            } else {
                VStack(spacing: 0) {
                    ForEach(window.visible(runs)) { run in
                        Button { onSelect(run) } label: {
                            RunRow(run: run, isSelected: selectedRunID == run.id)
                        }
                        .buttonStyle(.plainHit)
                    }
                }
                showMoreRow(runs)
            }
        }
        .editorialCard(padding: 18)
        .onChange(of: search) { window.reset() }
        .onChange(of: typeFilter) { window.reset() }
        .onChange(of: dayFilter) { window.reset() }
    }

    /// "Show 30 more" under the list while older runs are not built yet.
    @ViewBuilder private func showMoreRow(_ runs: [Run]) -> some View {
        if let summary = window.summary(in: runs) {
            HStack(spacing: 10) {
                Button { window.showMore() } label: {
                    Label("Show \(window.nextPageCount(in: runs)) more", systemImage: "chevron.down")
                        .font(DS.font(.caption, weight: .medium))
                }
                .buttonStyle(.borderless)
                Text(summary)
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p4)
                Spacer(minLength: 0)
            }
            .padding(.top, 10)
            .padding(.horizontal, 4)
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            Text(Self.heading.uppercased())
                .font(DS.font(.caption, weight: .medium))
                .tracking(0.06 * 11)
                .foregroundStyle(DS.Ink.p4)
            Spacer()
            searchField
            typeFilterMenu
        }
        .padding(.bottom, 14)
    }

    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .font(DS.font(.caption2))
                .foregroundStyle(DS.Ink.p4)
            TextField(Self.searchPrompt, text: $search)
                .textFieldStyle(.plain)
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p1)
                .frame(width: 160)
        }
        .padding(.horizontal, 8)
        .frame(height: 24)
        .background(
            RoundedRectangle(cornerRadius: 5).fill(DS.Paper.sunk)
                .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
    }

    /// The list's words: it lists runs, and Sessions is another page.
    static let heading = "Runs"
    static let searchPrompt = "Search runs"
    static let emptyText = "No runs match the current filters."

    /// Search matches what the row shows ("morning briefing", "didn't
    /// finish", "Nov 12"), the engine name (the ledger's slot key, such as
    /// `overnight-research`, and its slot-or-mode `ledgerKey`, or the
    /// log-guessed `morningBriefing` for a run without a ledger row) and
    /// commit subjects. A run the ledger names doesn't also match its
    /// log-name guess, so "morning" doesn't find an overnight run (T2).
    static func matches(_ run: Run, _ search: String) -> Bool {
        let engineNames = run.ledgerName == nil && run.ledgerKey == nil
            ? [run.type.rawValue]
            : Array(Set([run.slotKey, run.ledgerKey].compactMap { $0 })).sorted()
        let hay = [
            run.displayName, run.statusDisplayName,
            run.startedAt.formatted(date: .abbreviated, time: .omitted),
        ] + engineNames + ProtectionBadge.kinds(for: run).map(\.title) + run.commits.map(\.subject)
        return hay.joined(separator: " ").localizedCaseInsensitiveContains(search)
    }

    @ViewBuilder private var typeFilterMenu: some View {
        let label = typeFilter.count == RunType.allCases.count
            ? "Type: all"
            : "Type: \(typeFilter.count)"
        Menu(label) {
            ForEach(RunType.allCases, id: \.self) { t in
                Toggle(t.displayName, isOn: Binding(
                    get: { typeFilter.contains(t) },
                    set: { v in
                        if v { typeFilter.insert(t) } else { typeFilter.remove(t) }
                    }
                ))
            }
        }
        .menuStyle(.button)
        .font(DS.font(.label))
        .frame(height: 24)
    }

    private var emptyState: some View {
        Text(Self.emptyText)
            .dsFont(.body, .serif)
            .foregroundStyle(DS.Ink.p3)
            .italic()
            .padding(.vertical, 16)
    }
}
