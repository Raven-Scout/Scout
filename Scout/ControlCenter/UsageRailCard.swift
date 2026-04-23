import SwiftUI

/// Rail card replacing the removed `BudgetRailCard`. Renders today's and
/// this-week's token totals plus a per-model share line. Dollar cost is
/// intentionally hidden — it's a misleading metric on a Claude team-plan
/// seat (quota-based; dollars only apply to overage). See the
/// 2026-04-22 design spec under docs/superpowers/specs/.
struct UsageRailCard: View {
    @EnvironmentObject var state: AppState

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
    }

    // MARK: - Totals

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

