import SwiftUI

/// Three-up hero: Now · Today · Next up. Each column shares the same
/// typographic block — small uppercase label, large serif value, mono sub.
struct NowStripView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        // CC-Hero: previous HStack used the default `.center` alignment,
        // which let the column dividers (flexible-height Rectangles) stretch
        // the card to ~360 px while pushing the actual text content into
        // vertical centre. Pin to `.top` and give the dividers a fixed
        // height so the card's height tracks the text content (~70 px).
        HStack(alignment: .top, spacing: 0) {
            column(label: "Now") { nowColumn }
            divider
            column(label: "Today") { todayColumn }
            divider
            column(label: "Next up") { nextColumn }
        }
        .editorialCard(padding: 14)
    }

    // MARK: - Column layout helper

    private func column<Content: View>(label: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(label.uppercased())
                .font(DS.sans(10.5, weight: .medium))
                .tracking(0.08 * 10.5)
                .foregroundStyle(DS.Ink.p4)
                .padding(.bottom, 6)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 18)
    }

    private var divider: some View {
        Rectangle()
            .fill(DS.Rule.soft)
            .frame(width: 0.5, height: 56)
    }

    // MARK: - Columns

    @ViewBuilder private var nowColumn: some View {
        // CC-1: prefer a *fresh* running run if one exists; otherwise fall
        // through stale/orphaned heads to the most-recent run that actually
        // resolved. Without this, a session whose finish marker the runner
        // failed to write would latch the hero on "running · started 4 h ago"
        // even though a later run of the same type already completed cleanly.
        let runs = state.sessionLogService.runs
        let live = runs.first { $0.status == .running }
        let resolved = runs.first { $0.status != .running && $0.status != .orphaned }
        VStack(alignment: .leading, spacing: 4) {
            if let r = live {
                bigName(r.displayName)
                sub("running · started \(r.startedAt.formatted(.relative(presentation: .named)))", color: DS.Status.warn)
            } else if let r = resolved {
                bigName(r.displayName)
                HStack(spacing: 4) {
                    Image(systemName: statusIcon(for: r.status)).imageScale(.small)
                    Text("\(r.status.rawValue) · \(r.startedAt.formatted(.relative(presentation: .named))) · \(r.commits.count) commit\(r.commits.count == 1 ? "" : "s")")
                }
                .font(DS.mono(12))
                .foregroundStyle(r.status == .success ? DS.Status.ok : DS.Status.err)
            } else if let r = runs.first {
                // Only orphaned entries on record — say so explicitly rather
                // than pretending the latest is fine.
                bigName(r.displayName)
                sub("orphaned · started \(r.startedAt.formatted(.relative(presentation: .named)))", color: DS.Ink.p4)
            } else {
                bigName("No runs yet")
                sub("Scout is quiet", color: DS.Ink.p4)
            }
        }
    }

    @ViewBuilder private var todayColumn: some View {
        let runs = todayRuns()
        let failures = runs.filter { [.failure, .timeout, .rateLimited].contains($0.status) }.count
        let total = runs.compactMap(\.cost).reduce(Decimal(0), +)
        VStack(alignment: .leading, spacing: 4) {
            bigName("\(runs.count) runs · \(failures) failed")
            sub("cost $\(total as NSDecimalNumber) · budget $8.00", color: DS.Ink.p3)
        }
    }

    @ViewBuilder private var nextColumn: some View {
        // CC-2: `upcoming` is now sorted by `scheduledAt` ascending and has
        // past entries already filtered out by ScheduleService, so the first
        // non-manual entry is the actual soonest scheduled run. Defensive
        // `> Date()` check kept in case the array is briefly stale during a
        // refresh tick.
        let now = Date()
        let next = state.scheduleService.upcoming.first {
            $0.type != .manual && $0.scheduledAt > now
        }
        VStack(alignment: .leading, spacing: 4) {
            if let u = next {
                bigName(u.type.displayName)
                sub("\(u.scheduledAt.formatted(.relative(presentation: .named))) · dispatcher armed", color: DS.Ink.p3)
            } else {
                bigName("No scheduled runs")
                sub("check LaunchAgents", color: DS.Status.warn)
            }
        }
    }

    // MARK: - Text atoms

    private func bigName(_ s: String) -> some View {
        Text(s)
            .font(DS.serif(22, weight: .medium))
            .foregroundStyle(DS.Ink.p1)
            .lineLimit(1)
            .truncationMode(.tail)
    }

    private func sub(_ s: String, color: Color) -> some View {
        Text(s)
            .font(DS.mono(12))
            .foregroundStyle(color)
    }

    private func statusIcon(for status: RunStatus) -> String {
        status == .success ? "checkmark" : status == .running ? "circle.fill" : "xmark"
    }

    private func todayRuns() -> [Run] {
        let cal = Calendar.current
        return state.sessionLogService.runs.filter { cal.isDateInToday($0.startedAt) }
    }
}
