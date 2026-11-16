import Combine
import SwiftUI

/// Persistent bottom strip across the main window — Scout's status, next-run
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

    /// `AppState.status`, the same status as the menu-bar icon and panel.
    /// When it has a fix, the cell is a button to Settings, where both
    /// "Set up…" and the engine details live.
    private var health: some View {
        let status = state.status
        let color = status.tone.color
        return Button {
            state.requestedSidebar = .settings
        } label: {
            HStack(spacing: 6) {
                Circle()
                    .fill(color)
                    .frame(width: 6, height: 6)
                    .overlay(
                        Circle()
                            .strokeBorder(color.opacity(0.22), lineWidth: 2)
                            .frame(width: 10, height: 10)
                    )
                Text("Scout ·")
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Ink.p3)
                Text(status.title)
                    .font(DS.sans(11, weight: .medium))
                    .foregroundStyle(DS.Ink.p2)
                    .lineLimit(1)
                if let fix = status.fix {
                    Text(fix.title)
                        .font(DS.sans(11, weight: .medium))
                        .foregroundStyle(DS.Accent.ink)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(status.fix == nil)
        .help(status.detail)
        .accessibilityLabel("Scout: \(status.title). \(status.detail)")
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
            Text("today")
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

    private var nextRunValue: String {
        guard let next = state.scheduleService.upcoming.first(where: { $0.type != .manual }) else {
            return "—"
        }
        let mins = max(0, Int(next.scheduledAt.timeIntervalSince(now) / 60))
        if mins < 60 { return "\(next.type.displayName) · \(mins)m" }
        let hh = mins / 60, mm = mins % 60
        return "\(next.type.displayName) · \(hh)h\(mm > 0 ? " \(mm)m" : "")"
    }

    private var todayCostValue: String {
        let cal = Calendar.current
        let runs = state.sessionLogService.runs.filter { cal.isDateInToday($0.startedAt) }
        let total = runs.compactMap(\.cost).reduce(Decimal(0), +)
        let nsTotal = NSDecimalNumber(decimal: total)
        let fmt = NumberFormatter()
        fmt.numberStyle = .currency
        fmt.currencyCode = "USD"
        fmt.maximumFractionDigits = 2
        return fmt.string(from: nsTotal) ?? "$0.00"
    }
}

extension ScoutStatus.Tone {
    /// The one colour per tone the icon's panel and the status bar share.
    var color: Color {
        switch self {
        case .ok:      return DS.Status.ok
        case .warn:    return DS.Status.warn
        case .err:     return DS.Status.err
        case .active:  return DS.Accent.fill
        case .neutral: return DS.Ink.p4
        }
    }
}
