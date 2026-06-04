import SwiftUI

/// Sessions table — editorial card with search + type filter and a flat row
/// layout (status icon · name/when · status · commits · cost).
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

    var filtered: [Run] {
        let cal = Calendar.current
        return state.sessionLogService.runs.filter { run in
            if let d = dayFilter, !cal.isDate(run.startedAt, inSameDayAs: d) { return false }
            if !typeFilter.contains(run.type) { return false }
            if !search.isEmpty {
                let hay = "\(run.type.rawValue) \(run.commits.map(\.subject).joined(separator: " "))"
                if !hay.localizedCaseInsensitiveContains(search) { return false }
            }
            return true
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if filtered.isEmpty {
                emptyState
            } else {
                VStack(spacing: 0) {
                    ForEach(filtered.prefix(30)) { run in
                        Button { onSelect(run) } label: {
                            RunRow(run: run, isSelected: selectedRunID == run.id)
                        }
                        .buttonStyle(.plainHit)
                    }
                }
            }
        }
        .editorialCard(padding: 18)
    }

    private var header: some View {
        HStack(spacing: 8) {
            Text("Sessions".uppercased())
                .font(DS.sans(11, weight: .medium))
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
                .font(.system(size: 10))
                .foregroundStyle(DS.Ink.p4)
            TextField("Search sessions", text: $search)
                .textFieldStyle(.plain)
                .font(DS.sans(12))
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

    @ViewBuilder private var typeFilterMenu: some View {
        let label = typeFilter.count == RunType.allCases.count
            ? "Type: all"
            : "Type: \(typeFilter.count)"
        Menu(label) {
            ForEach(RunType.allCases, id: \.self) { t in
                Toggle(t.rawValue, isOn: Binding(
                    get: { typeFilter.contains(t) },
                    set: { v in
                        if v { typeFilter.insert(t) } else { typeFilter.remove(t) }
                    }
                ))
            }
        }
        .menuStyle(.button)
        .font(DS.sans(12))
        .frame(height: 24)
    }

    private var emptyState: some View {
        Text("No sessions match the current filters.")
            .font(DS.serif(13))
            .foregroundStyle(DS.Ink.p3)
            .italic()
            .padding(.vertical, 16)
    }
}
