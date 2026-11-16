import SwiftUI

/// Red banner stretched across the top of `ControlCenterView`, visible
/// only when `ConnectorHealthService.activeAlerts` is non-empty. Click →
/// popover with reason text + an "Acknowledge" button. Acking dismisses
/// the banner in-app; the underlying alert still fires in scheduled-run
/// Slack DMs until the connector recovers.
struct ConnectorAlertBanner: View {
    @EnvironmentObject var state: AppState
    @State private var showPopover: Bool = false

    var body: some View {
        let alerts = state.connectorHealthService.activeAlerts
        if alerts.isEmpty {
            EmptyView()
        } else {
            bannerView(alerts: alerts)
        }
    }

    private func bannerView(alerts: [ConnectorAlert]) -> some View {
        let head = alerts[0]
        let more = alerts.count - 1
        return Button {
            showPopover = true
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "exclamationmark.triangle").font(DS.mono(13))
                Text(summary(for: head, moreCount: more))
                    .font(DS.mono(12))
                    .lineLimit(1)
                    .foregroundStyle(.white)
                Spacer()
                Text("Details")
                    .font(DS.mono(11))
                    .foregroundStyle(.white.opacity(0.8))
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(Color(red: 0.75, green: 0.15, blue: 0.15))
        }
        .buttonStyle(.plainHit)
        .popover(isPresented: $showPopover, arrowEdge: .bottom) {
            popoverContent(alerts: alerts)
        }
    }

    private func popoverContent(alerts: [ConnectorAlert]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(alerts, id: \.fingerprint) { alert in
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(DisplayName.connector(alert.connector)): \(alert.level.displayName.lowercased())")
                        .font(DS.sans(12, weight: .semibold))
                        .foregroundStyle(DS.Ink.p1)
                        .help(alert.connector)
                    Text(Self.plainReason(alert.reason))
                        .font(DS.sans(11.5))
                        .foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("What to do: reconnect \(DisplayName.connector(alert.connector)) in Claude Code (type /mcp in a Claude Code session), then wait for the next run.")
                        .font(DS.sans(11.5))
                        .foregroundStyle(DS.Ink.p1)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("Acknowledge") {
                        state.connectorHealthService.acknowledge(fingerprint: alert.fingerprint)
                    }
                    .font(DS.sans(11))
                    .padding(.top, 4)
                }
                .padding(.bottom, 8)
            }
        }
        .padding(14)
        .frame(width: 360)
    }

    private func summary(for alert: ConnectorAlert, moreCount: Int) -> String {
        let base = "\(DisplayName.connector(alert.connector)) \(alert.level == .critical ? "isn't working" : "has a problem"): \(Self.plainReason(alert.reason))"
        return moreCount > 0 ? "\(base)  (+\(moreCount) more)" : base
    }

    /// The engine's reason without its Markdown code ticks.
    static func plainReason(_ reason: String) -> String {
        reason.replacingOccurrences(of: "`", with: "")
    }
}
