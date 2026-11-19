import SwiftUI

/// Banner stretched across the top of `ControlCenterView`, visible only when
/// `ConnectorHealthService.activeAlerts` is non-empty. Click → popover with
/// each alert's reason and fix, an "Acknowledge" per alert and "Acknowledge
/// all". Acking dismisses the banner in-app; the underlying alert still fires
/// in scheduled-run Slack DMs until the connector recovers.
struct ConnectorAlertBanner: View {
    @EnvironmentObject var state: AppState
    @State private var showPopover: Bool = false

    var body: some View {
        let alerts = state.connectorHealthService.activeAlerts
        if alerts.isEmpty {
            EmptyView()
        } else {
            bannerView(alerts: Self.ordered(alerts))
        }
    }

    /// Critical alerts make a red bar; warnings alone an amber wash with dark
    /// text, so a connector that is only degraded doesn't read like one that
    /// is down (UI audit R10, #28).
    private func bannerView(alerts: [ConnectorAlert]) -> some View {
        let head = alerts[0]
        let more = alerts.count - 1
        let critical = Self.isCritical(alerts)
        let ink = critical ? DS.Status.onFill : DS.Ink.p1
        let text = summary(for: head, moreCount: more)
        return Button {
            showPopover = true
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "exclamationmark.triangle").font(DS.font(.body))
                    .foregroundStyle(critical ? DS.Status.onFill : DS.Status.warn)
                    .accessibilityHidden(true)
                Text(text)
                    .font(DS.font(.label))
                    .lineLimit(1)
                    .foregroundStyle(ink)
                Spacer()
                Text("Details")
                    .font(DS.font(.caption))
                    .foregroundStyle(ink)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(critical ? DS.Status.err : DS.Status.warn.opacity(DS.Status.washOpacity))
        }
        .buttonStyle(.plainHit) // ds-guard: hit area — the whole banner opens the details
        .accessibilityLabel(text)
        .accessibilityHint("Shows every connector problem")
        .popover(isPresented: $showPopover, arrowEdge: .bottom) {
            popoverContent(alerts: alerts)
        }
    }

    private func popoverContent(alerts: [ConnectorAlert]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            if alerts.count > 1 {
                HStack {
                    Text("\(alerts.count) connector problems")
                        .font(DS.font(.label, weight: .semibold))
                        .foregroundStyle(DS.Ink.p1)
                    Spacer()
                    Button("Acknowledge all") {
                        for alert in alerts {
                            state.connectorHealthService.acknowledge(fingerprint: alert.fingerprint)
                        }
                        showPopover = false
                    }
                    .buttonStyle(.ds(.secondary, size: .small))
                    .help("Hide these alerts until a connector has a new problem")
                }
                .padding(14)
                EditorialRule()
            }
            // A long list scrolls instead of growing past the screen.
            ScrollView {
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
                            .buttonStyle(.ds(.link, size: .small))
                            .padding(.top, 4)
                        }
                        .padding(.bottom, 8)
                    }
                }
                .padding(14)
            }
            .frame(maxHeight: 420)
        }
        .frame(width: 360)
    }

    /// True when any alert is critical: only then is the banner red.
    static func isCritical(_ alerts: [ConnectorAlert]) -> Bool {
        alerts.contains { $0.level == .critical }
    }

    /// Critical alerts first (otherwise as given), so the banner names the worst.
    static func ordered(_ alerts: [ConnectorAlert]) -> [ConnectorAlert] {
        alerts.enumerated().sorted { a, b in
            let ra = a.element.level == .critical ? 0 : 1
            let rb = b.element.level == .critical ? 0 : 1
            return (ra, a.offset) < (rb, b.offset)
        }.map(\.element)
    }

    private func summary(for alert: ConnectorAlert, moreCount: Int) -> String {
        let p = Problem(connectorAlert: alert)
        let base = "\(p.title): \(p.impact ?? "")"
        return moreCount > 0 ? "\(base)  (+\(moreCount) more)" : base
    }
}
