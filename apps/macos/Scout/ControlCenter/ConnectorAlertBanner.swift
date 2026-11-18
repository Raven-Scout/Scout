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
                    .foregroundStyle(DS.Status.onFill).accessibilityHidden(true)
                Text(summary(for: head, moreCount: more))
                    .font(DS.mono(12))
                    .lineLimit(1)
                    .foregroundStyle(DS.Status.onFill)
                Spacer()
                Text("Details")
                    .font(DS.mono(11))
                    .foregroundStyle(DS.Status.onFill)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(DS.Status.err)
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
                    // Per-connector fix (claude.ai, gh, the Chrome extension…)
                    // from `DisplayName.connectorFix`; the roster's own
                    // command-level advice is behind Details.
                    ProblemView(problem: Problem(connectorAlert: alert), style: .inline)
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
        let p = Problem(connectorAlert: alert)
        let base = "\(p.title): \(p.impact ?? "")"
        return moreCount > 0 ? "\(base)  (+\(moreCount) more)" : base
    }
}
