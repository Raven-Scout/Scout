import SwiftUI

/// Heartbeat schedule — a clean editorial table, one row per upcoming run.
struct UpcomingStripView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                Text("Heartbeat schedule")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(0.06 * 11)
                    .foregroundStyle(DS.Ink.p4)
                Spacer()
            }
            .padding(.bottom, 14)

            let scheduled = state.scheduleService.upcoming.filter { $0.type != .manual }
            if scheduled.isEmpty {
                emptyState
            } else {
                VStack(spacing: 0) {
                    ForEach(scheduled.prefix(6)) { up in
                        row(up)
                    }
                }
            }

            EditorialRule().padding(.top, 10)

            footerRow
        }
        .editorialCard(padding: 18)
    }

    private func row(_ up: UpcomingRun) -> some View {
        HStack(alignment: .center, spacing: 0) {
            timeCell(up)
                .frame(width: 90, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text(up.type.displayName)
                    .font(DS.sans(13, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Text(subtitle(for: up.type))
                    .font(DS.mono(11.5))
                    .foregroundStyle(DS.Ink.p3)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text("queued")
                .font(DS.mono(11, weight: .medium))
                .tracking(0.04 * 11)
                .foregroundStyle(DS.Ink.p3)
                .padding(.trailing, 10)
            Button("Run now") {
                Task {
                    await state.fireNow(slotKey: up.slotKey, bypassBudget: false)
                }
            }
            .buttonStyle(.plain)
            .font(DS.sans(11, weight: .medium))
            .foregroundStyle(DS.Ink.p2)
            .padding(.horizontal, 8)
            .frame(height: 22)
            .background(EditorialChipBackground())
        }
        .padding(.vertical, 10)
        .overlay(alignment: .bottom) { EditorialRule() }
    }

    private func timeCell(_ up: UpcomingRun) -> some View {
        let fmt = DateFormatter()
        fmt.dateFormat = "h:mm a"
        fmt.amSymbol = "AM"; fmt.pmSymbol = "PM"
        let day = DateFormatter()
        day.dateFormat = "MMM d"
        return VStack(alignment: .leading, spacing: 2) {
            Text(fmt.string(from: up.scheduledAt))
                .font(DS.mono(12.5, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
            Text(day.string(from: up.scheduledAt))
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p4)
        }
    }

    private func subtitle(for type: RunType) -> String {
        let s = type.rawValue.lowercased()
        if s.contains("consolidation") { return "rollup + tagging" }
        if s.contains("dreaming")      { return "long-form synthesis" }
        if s.contains("briefing")      { return "morning run" }
        if s.contains("research")      { return "web + papers" }
        return ""
    }

    private var emptyState: some View {
        Text("No upcoming runs returned by scoutctl.")
            .font(DS.serif(13))
            .foregroundStyle(DS.Ink.p3)
            .italic()
            .padding(.vertical, 16)
    }

    private var footerRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Schedule polled from `scoutctl schedule list-upcoming` every 60 s.")
                .font(DS.sans(12))
                .foregroundStyle(DS.Ink.p3)
            if !state.scheduleService.upcoming.contains(where: { $0.type == .research }) {
                Text("Research: no schedule configured")
                    .font(DS.sans(12))
                    .foregroundStyle(DS.Status.warn)
            }
        }
        .padding(.top, 10)
    }
}
