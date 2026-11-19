import Combine
import SwiftUI

/// "Today's usage": what Scout's own scheduled runs used today and this week,
/// plus today's tool activity (CC-6).
///
/// Tokens and cost come from the engine (`scoutctl budget usage --json`, via
/// `ScoutUsageService`), which counts finished scheduled runs only — never the
/// owner's own Claude sessions — and counts each message once. The card used to
/// sum a per-session token log itself and showed ~270,000M tokens a day on a
/// real vault (audit card C5). Cache traffic is shown apart from input and
/// output, and the cost line says how the figure was found, so an unmeasured
/// $0 never reads as a measurement.
///
/// Tool stats are sourced from `ClaudeSessionService.aggregateStats` against
/// today's runs and refresh whenever the run list changes.
struct UsageRailCard: View {
    @EnvironmentObject var state: AppState

    @State private var stats: ClaudeSessionService.AggregateStats?
    @State private var loadingStats: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            RailCardHeader(title: "Today's usage")
            usageSection

            Divider().padding(.vertical, 10)

            statsSection
        }
        .editorialCard(padding: 16)
        .task(id: todayRunsKey) {
            await state.scoutUsageService.refresh()
            await loadStats()
        }
    }

    // MARK: - Usage section

    @ViewBuilder
    private var usageSection: some View {
        if let summary = state.scoutUsageService.summary {
            let today = summary.today
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(UsageFormat.tokens(today.tokens.inputOutput))
                    .dsFont(.hero, .serif, weight: .medium)
                    .foregroundStyle(DS.Ink.p1)
                Text("tokens in + out")
                    .font(DS.font(.body))
                    .foregroundStyle(DS.Ink.p4)
            }
            .accessibilityElement(children: .combine)
            Text(UsageFormat.cacheLine(today.tokens))
                .font(DS.font(.caption, .mono))
                .foregroundStyle(DS.Ink.p3)
                .padding(.top, 6)
                .fixedSize(horizontal: false, vertical: true)
            Text("cost " + UsageFormat.cost(today))
                .font(DS.font(.caption, .mono))
                .foregroundStyle(DS.Ink.p2)
                .padding(.top, 4)
                .fixedSize(horizontal: false, vertical: true)
            Text("Scout's scheduled runs only: \(UsageFormat.coverage(today)). Your own Claude sessions aren't counted.")
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p3)
                .padding(.top, 6)
                .fixedSize(horizontal: false, vertical: true)

            Divider().padding(.vertical, 10)

            let week = summary.week
            Text("This week: \(UsageFormat.tokens(week.tokens.inputOutput)) in + out · cache \(UsageFormat.tokens(week.tokens.cache))")
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p3)
                .fixedSize(horizontal: false, vertical: true)
            Text("cost " + UsageFormat.cost(week))
                .font(DS.font(.caption, .mono))
                .foregroundStyle(DS.Ink.p4)
                .padding(.top, 2)
                .fixedSize(horizontal: false, vertical: true)
        } else if let error = state.scoutUsageService.lastError {
            Text("Usage isn't available: \(error)")
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p3)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            Text("Loading usage…")
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p4)
        }
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
                        .font(DS.font(.caption2))
                        .foregroundStyle(DS.Ink.p4)
                        .padding(.top, 4)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else if loadingStats {
            Text("Loading session activity…")
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p4)
        } else {
            Text("No tool activity recorded today.")
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p4)
        }
    }

    private func statRow(label: String, value: String) -> some View {
        HStack {
            Text(label)
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
            Text(value)
                .font(DS.font(.caption, .mono, weight: .medium))
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
}
