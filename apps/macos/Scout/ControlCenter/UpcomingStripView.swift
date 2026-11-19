import SwiftUI

/// Heartbeat schedule — a clean editorial table, one row per upcoming run.
struct UpcomingStripView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                Text("Heartbeat schedule")
                    .font(DS.font(.caption, weight: .medium))
                    .tracking(0.06 * 11)
                    .foregroundStyle(DS.Ink.p4)
                Spacer()
            }
            .padding(.bottom, 14)

            // CC-3: ScheduleService now publishes chronologically sorted,
            // future-only entries, so this filter is just "skip manual"
            // and the slice picks the soonest 4 (was 6, too dense).
            let scheduled = state.scheduleService.upcoming.filter { $0.type != .manual }
            if scheduled.isEmpty {
                emptyState
            } else {
                VStack(spacing: 0) {
                    ForEach(scheduled.prefix(4)) { up in
                        row(up)
                    }
                }
            }

            if let fireErr = state.fireNowError {
                // #45: a failed "Run now" used to be swallowed silently. Surface
                // it inline so the user knows the dispatch didn't take.
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(DS.Status.warn)
                    Text(fireErr)
                        .font(DS.font(.caption))
                        .foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .padding(.top, 10)
            }

            EditorialRule().padding(.top, 10)

            footerRow
        }
        .editorialCard(padding: 18)
    }

    private func row(_ up: UpcomingRun) -> some View {
        // CC-3: tighter row — drop the redundant "queued" pill (everything in
        // this list is queued by definition) and shrink vertical padding so
        // the section costs less of the page.
        HStack(alignment: .center, spacing: 12) {
            timeCell(up)
                .frame(width: 88, alignment: .leading)
            VStack(alignment: .leading, spacing: 1) {
                Text(up.displayName)
                    .font(DS.font(.label, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Text(subtitle(for: up.type))
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p4)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button("Run now") {
                Task {
                    await state.fireNow(slotKey: up.slotKey)
                }
            }
            .buttonStyle(.ds(.secondary, size: .small))
            .accessibilityLabel("Run \(up.displayName) now")
        }
        .padding(.vertical, 6)
        .overlay(alignment: .bottom) { EditorialRule() }
    }

    private func timeCell(_ up: UpcomingRun) -> some View {
        let fmt = DateFormatter()
        fmt.dateFormat = "h:mm a"
        fmt.amSymbol = "AM"; fmt.pmSymbol = "PM"
        return VStack(alignment: .leading, spacing: 1) {
            Text(fmt.string(from: up.scheduledAt))
                .font(DS.font(.label, .mono, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
            Text(relativeDay(up.scheduledAt))
                .font(DS.font(.caption2))
                .foregroundStyle(DS.Ink.p4)
        }
    }

    /// Compact relative-day label: "today" / "tomorrow" / "MMM d". Saves a
    /// row of vertical space vs. always showing the absolute date.
    private func relativeDay(_ d: Date) -> String {
        let cal = Calendar.current
        if cal.isDateInToday(d) { return "today" }
        if cal.isDateInTomorrow(d) { return "tomorrow" }
        let fmt = DateFormatter()
        fmt.dateFormat = "MMM d"
        return fmt.string(from: d)
    }

    private func subtitle(for type: RunType) -> String {
        let s = type.rawValue.lowercased()
        if s.contains("consolidation") { return "catches up on changes" }
        if s.contains("dreaming")      { return "tidies notes" }
        if s.contains("briefing")      { return "today's action items" }
        if s.contains("research")      { return "news on your topics" }
        return ""
    }

    @ViewBuilder
    private var emptyState: some View {
        if let err = state.scheduleService.lastError {
            // scoutctl failed — show the actual reason instead of a generic
            // "nothing to show" message. Common cause on macOS: scoutctl
            // not on the .app bundle's PATH (now also fixed by resolving
            // the engine through EngineLocator instead of relying on PATH).
            ProblemView(problem: .engineUnavailable("The schedule", details: err),
                        actions: [.retry { Task { await state.scheduleService.refresh() } }],
                        style: .inline)
                .padding(.vertical, 14)
        } else {
            Text("Nothing is scheduled to run.")
                .dsFont(.body, .serif)
                .foregroundStyle(DS.Ink.p3)
                .italic()
                .padding(.vertical, 16)
        }
    }

    private var footerRow: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Updates every minute.")
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p3)
                .help("Read from the engine with `scoutctl schedule list-upcoming` every 60 seconds.")
            if !state.scheduleService.upcoming.contains(where: { $0.type == .research }) {
                Text("Research isn't scheduled.")
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Status.warn)
            }
        }
        .padding(.top, 10)
    }
}
