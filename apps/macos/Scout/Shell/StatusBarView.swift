import Combine
import SwiftUI

/// Persistent bottom strip across the main window — daemon health, next-run
/// ETA, today's cost, and the active view label. Matches the design's
/// .statusbar language from Scout.html (the handoff bundle).
///
/// All values are derived from live AppState services; the strip silently
/// downgrades when a value is unavailable (e.g. budget not yet loaded).
struct StatusBarView: View {
    @EnvironmentObject var state: AppState
    let viewLabel: String

    @State private var now: Date = Date()
    private let tick = Timer.publish(every: 30, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(spacing: 14) {
            health
            spacer
            nextRun
            spacer
            todayCost
            Spacer()
            viewItem
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .frame(height: 26)
        .background(
            LinearGradient(
                colors: [DS.Paper.base, DS.Paper.sunk],
                startPoint: .top,
                endPoint: .bottom
            )
        )
        .overlay(alignment: .top) { EditorialRule(color: DS.Rule.hard) }
        .onReceive(tick) { now = $0 }
    }

    // MARK: - Cells

    private var health: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(daemonColor)
                .frame(width: 6, height: 6)
                .shadow(color: daemonColor.opacity(0.4), radius: 0)
                .overlay(
                    Circle()
                        .strokeBorder(daemonColor.opacity(0.22), lineWidth: 2)
                        .frame(width: 10, height: 10)
                )
            Text("Scout daemon ·")
                .font(DS.sans(11))
                .foregroundStyle(DS.Ink.p3)
            Text(daemonLabel)
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p2)
        }
    }

    private var nextRun: some View {
        HStack(spacing: 6) {
            Text("next run")
                .font(DS.sans(11))
                .foregroundStyle(DS.Ink.p4)
            Text(nextRunValue)
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p2)
        }
    }

    private var todayCost: some View {
        HStack(spacing: 6) {
            Text("runs today")
                .font(DS.sans(11))
                .foregroundStyle(DS.Ink.p4)
            Text(todayCostValue)
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p2)
        }
    }

    private var viewItem: some View {
        HStack(spacing: 6) {
            Text("view")
                .font(DS.sans(11))
                .foregroundStyle(DS.Ink.p4)
            Text(viewLabel)
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p2)
        }
    }

    private var spacer: some View {
        Rectangle()
            .fill(DS.Rule.soft)
            .frame(width: 0.5, height: 12)
    }

    // MARK: - Derived values

    private var daemonColor: Color {
        switch state.menuBarStatus {
        case .running:        return DS.Status.warn
        case .lastFailed:     return DS.Status.err
        case .budgetSkipped:  return DS.Status.warn
        case .idle:           return DS.Status.ok
        }
    }

    private var daemonLabel: String {
        switch state.menuBarStatus {
        case .running:        return "running"
        case .lastFailed:     return "last run failed"
        case .budgetSkipped:  return "budget skip"
        case .idle:           return "healthy"
        }
    }

    private var nextRunValue: String {
        guard let next = state.scheduleService.upcoming.first(where: { $0.type != .manual }) else {
            return "—"
        }
        let mins = max(0, Int(next.scheduledAt.timeIntervalSince(now) / 60))
        if mins < 60 { return "\(next.type.displayName) · \(mins)m" }
        let hh = mins / 60, mm = mins % 60
        return "\(next.type.displayName) · \(hh)h\(mm > 0 ? " \(mm)m" : "")"
    }

    /// Today's cost of Scout's scheduled runs, from the same engine summary as
    /// the usage card, so the two never disagree. "≥" or "unknown" when some or
    /// all runs reported no cost, instead of a $0 that was never measured.
    private var todayCostValue: String {
        guard let today = state.scoutUsageService.summary?.today else { return "—" }
        return UsageFormat.shortCost(today)
    }
}
