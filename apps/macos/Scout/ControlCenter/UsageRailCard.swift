import Combine
import SwiftUI

/// Rail card replacing the removed `BudgetRailCard`. Renders today's token
/// totals AND today's tool-activity breakdown (CC-6). Dollar cost is
/// intentionally hidden — it's a misleading metric on a Claude team-plan
/// seat (quota-based; dollars only apply to overage). See the
/// 2026-04-22 design spec under docs/superpowers/specs/.
///
/// CC-6: token-only view was thin. Now also surfaces:
///   - total tool calls
///   - file edits + writes (unique paths)
///   - bash invocations, webfetch, websearch
///   - top tools by count
/// Stats are sourced from `ClaudeSessionService.aggregateStats` against
/// today's runs. Refreshes whenever the run list changes.
struct UsageRailCard: View {
    @EnvironmentObject var state: AppState

    @State private var stats: ClaudeSessionService.AggregateStats?
    @State private var loadingStats: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            RailCardHeader(title: "Today's usage")
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(formatTokens(todayTotals.allTokens))
                    .font(DS.serif(22, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Text("tokens")
                    .font(DS.mono(13))
                    .foregroundStyle(DS.Ink.p4)
            }
            Text(splitLine(todayTotals))
                .font(DS.mono(11.5))
                .foregroundStyle(DS.Ink.p3)
                .padding(.top, 6)
                .fixedSize(horizontal: false, vertical: true)

            Divider().padding(.vertical, 10)

            statsSection

            Divider().padding(.vertical, 10)

            Text("Week: \(formatTokens(weekTotals.allTokens)) tokens")
                .font(DS.mono(11.5))
                .foregroundStyle(DS.Ink.p3)
            Text(modelShareLine(weekTotals))
                .font(DS.mono(11.5))
                .foregroundStyle(DS.Ink.p4)
                .padding(.top, 2)

            Text("Quota: TBD (Phase 2)")
                .font(DS.mono(10.5))
                .foregroundStyle(DS.Ink.p4.opacity(0.7))
                .padding(.top, 10)
        }
        .editorialCard(padding: 16)
        .task(id: todayRunsKey) { await loadStats() }
    }

    // MARK: - Stats section

    @ViewBuilder
    private var statsSection: some View {
        if let s = stats {
            VStack(alignment: .leading, spacing: 4) {
                statRow(label: "tool calls",  value: "\(s.totalToolCalls)")
                if s.fileMutations > 0 {
                    statRow(label: "files edited", value: "\(s.fileMutations)")
                }
                if s.bashCalls > 0 {
                    statRow(label: "bash",        value: "\(s.bashCalls)")
                }
                if s.webFetches + s.webSearches > 0 {
                    statRow(label: "web",         value: "\(s.webFetches + s.webSearches)")
                }
                if !s.topTools.isEmpty {
                    Text("Top: " + s.topTools.map { "\($0.name) \($0.count)" }.joined(separator: " · "))
                        .font(DS.mono(10.5))
                        .foregroundStyle(DS.Ink.p4)
                        .padding(.top, 4)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else if loadingStats {
            Text("Loading session activity…")
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p4)
        } else {
            Text("No tool activity recorded today.")
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p4)
        }
    }

    private func statRow(label: String, value: String) -> some View {
        HStack {
            Text(label)
                .font(DS.mono(11.5))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
            Text(value)
                .font(DS.mono(11.5, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
        }
    }

    // MARK: - Data

    /// Keys this view's `.task(id:)` modifier to today's run set so we
    /// re-aggregate whenever a run finishes (and not on every render).
    private var todayRunsKey: String {
        todayRuns.map(\.id).joined(separator: "|")
    }

    private var todayRuns: [Run] {
        let cal = Calendar.current
        return state.sessionLogService.runs.filter {
            cal.isDateInToday($0.startedAt)
        }
    }

    private func loadStats() async {
        loadingStats = true
        let result = await state.claudeSessionService.aggregateStats(for: todayRuns)
        await MainActor.run {
            stats = result
            loadingStats = false
        }
    }

    private var todayTotals: TokenTotals {
        let (start, end) = etTodayRange()
        return state.sessionTokensService.totals(in: start..<end)
    }

    private var weekTotals: TokenTotals {
        let (start, end) = etCurrentWeekRange()
        return state.sessionTokensService.totals(in: start..<end)
    }

    // MARK: - Formatting

    private func formatTokens(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 1_000     { return String(format: "%.0fK", Double(n) / 1_000) }
        return "\(n)"
    }

    private func splitLine(_ t: TokenTotals) -> String {
        "in \(formatTokens(t.inputTokens)) · out \(formatTokens(t.outputTokens)) · cache-r \(formatTokens(t.cacheReadTokens)) · cache-c \(formatTokens(t.cacheCreationTokens))"
    }

    private func modelShareLine(_ t: TokenTotals) -> String {
        let opusPct = Int((t.modelShare(startingWith: "claude-opus") * 100).rounded())
        let sonnetPct = Int((t.modelShare(startingWith: "claude-sonnet") * 100).rounded())
        return "opus \(opusPct)% · sonnet \(sonnetPct)%"
    }

    // MARK: - ET date ranges

    private func etTodayRange() -> (Date, Date) {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        let start = cal.startOfDay(for: Date())
        let end = cal.date(byAdding: .day, value: 1, to: start)!
        return (start, end)
    }

    private func etCurrentWeekRange() -> (Date, Date) {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        cal.firstWeekday = 2 // Monday
        let now = Date()
        let comps = cal.dateComponents([.yearForWeekOfYear, .weekOfYear], from: now)
        let start = cal.date(from: comps)!
        let end = cal.date(byAdding: .day, value: 7, to: start)!
        return (start, end)
    }
}
