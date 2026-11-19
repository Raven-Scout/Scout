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
        // The usage card loads the summary too, but a side detail can hide it; the tile
        // mustn't sit on "cost —" then (R1 review).
        .task(id: todayRunsKey) { await state.scoutUsageService.refresh() }
    }

    /// Today's runs by the vault's day (the usage summary's `today` bounds), so the run count
    /// and the cost beside it count the same day. The host's day until the summary loads, and
    /// when its day is over (a refresh after midnight failed): never yesterday's count.
    nonisolated static func todayRuns(_ runs: [Run], today: UsagePeriod?, now: Date = Date(),
                                      calendar: Calendar = .current) -> [Run] {
        let current = today?.ifCurrent(at: now)
        return runs.filter { current?.contains($0.startedAt) ?? calendar.isDate($0.startedAt, inSameDayAs: now) }
    }

    /// Today's date as well as its runs: a render after midnight re-reads the summary even
    /// when no run has finished yet (the service's own rollover refresh can fail).
    private var todayRunsKey: String {
        let day = Calendar.current.startOfDay(for: Date()).timeIntervalSince1970
        return "\(day)|" + todayRuns().map(\.id).joined(separator: "|")
    }

    /// "cost ≥$42.09 · budget $8/day". The cost is today's from the engine's usage summary
    /// (`scoutctl budget usage`), the figure the usage card and the status bar show, so a
    /// cost no run measured reads "unknown" or "≥", never $0 (UI audit R1). "—" until it loads.
    /// The budget comes from the vault's own budget settings (`scoutctl budget show`), and is
    /// left off until they have loaded — never a made-up figure (card C3).
    nonisolated static func costLine(today: UsagePeriod?, dailyBudgetUSD: Double?) -> String {
        let cost: String
        if let today {
            cost = today.costKind == UsageCostKind.none ? "no cost yet" : "cost \(UsageFormat.shortCost(today))"
        } else {
            cost = "cost —"
        }
        guard let daily = dailyBudgetUSD, daily > 0 else { return cost }
        return "\(cost) · budget \(daily.formatted(.currency(code: "USD").precision(.fractionLength(0...2))))/day"
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
                // One status model (UI audit R10, #21): a routine skip is
                // amber with a pause, not a red ✗.
                HStack(spacing: 4) {
                    Image(systemName: RunOutcomeDisplay.symbol(r.status)).imageScale(.small)
                        .accessibilityHidden(true)
                    Text("\(r.status.displayName) · \(r.startedAt.formatted(.relative(presentation: .named))) · \(r.commits.count) commit\(r.commits.count == 1 ? "" : "s")")
                }
                .font(DS.mono(12))
                .foregroundStyle(RunOutcomeDisplay.tone(r.status).color)
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
        VStack(alignment: .leading, spacing: 4) {
            bigName(Self.todayHeadline(runs))
            // Yesterday's figure (a refresh after midnight failed) is no figure for today.
            sub(Self.costLine(today: state.scoutUsageService.summary?.today.ifCurrent(at: Date()),
                              dailyBudgetUSD: state.budgetSettingsService.settings?.dailyUSD),
                color: DS.Ink.p3)
        }
    }

    /// The Next column's sub-line: when the run starts, in words ("Starts in
    /// 3 hours"). It used to add "· dispatcher armed", an engine word (UI
    /// audit R11). A run whose time has come reads "Due now", never "Starts
    /// 5 minutes ago".
    nonisolated static func nextRunLine(_ scheduledAt: Date, now: Date = Date()) -> String {
        guard scheduledAt.timeIntervalSince(now) >= 60 else { return "Due now" }
        let formatter = RelativeDateTimeFormatter()
        formatter.dateTimeStyle = .named
        return "Starts \(formatter.localizedString(for: scheduledAt, relativeTo: now))"
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
                bigName(u.displayName)
                sub(Self.nextRunLine(u.scheduledAt), color: DS.Ink.p3)
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

    /// "5 runs · 1 failed", counted by `RunOutcomeDisplay.isFailure`, the
    /// rule the heatmap and the Signals card count with.
    nonisolated static func todayHeadline(_ runs: [Run]) -> String {
        "\(runs.count) run\(runs.count == 1 ? "" : "s") · \(RunOutcomeDisplay.failedCount(runs)) failed"
    }

    private func todayRuns() -> [Run] {
        Self.todayRuns(state.sessionLogService.runs, today: state.scoutUsageService.summary?.today)
    }
}
