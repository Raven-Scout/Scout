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
                Text("⚠").font(DS.mono(13))
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
        .buttonStyle(.plain)
        .popover(isPresented: $showPopover, arrowEdge: .bottom) {
            popoverContent(alerts: alerts)
        }
    }

    private func popoverContent(alerts: [ConnectorAlert]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(alerts, id: \.fingerprint) { alert in
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(alert.level.rawValue) · \(alert.connector)")
                        .font(DS.sans(12, weight: .semibold))
                        .foregroundStyle(DS.Ink.p1)
                    Text(alert.reason)
                        .font(DS.mono(11.5))
                        .foregroundStyle(DS.Ink.p3)
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
        let base = "\(displayName(for: alert.connector)) connector: \(alert.level.rawValue) — \(alert.reason)"
        return moreCount > 0 ? "\(base)  (+\(moreCount) more)" : base
    }

    private func displayName(for connector: String) -> String {
        // Same labels as the rail card's header row. Kept duplicated
        // locally to avoid a cross-file dependency just for 8 strings.
        switch connector {
        case "mcp:plugin_slack_slack":          return "Slack"
        case "mcp:plugin_linear_linear":        return "Linear"
        case "mcp:claude_ai_Gmail":             return "Gmail"
        case "mcp:claude_ai_Google_Calendar":   return "Calendar"
        case "mcp:claude_ai_Granola":           return "Granola"
        case "mcp:claude_ai_Google_Drive":      return "Drive"
        case "github":                          return "GitHub"
        case "mcp:claude-in-chrome":            return "Chrome"
        default:                                return connector
        }
    }
}
