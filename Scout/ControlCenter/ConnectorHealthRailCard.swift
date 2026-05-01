import SwiftUI

/// Matrix of connectors × last-5 sessions, with a 7d success-rate column.
/// Reads `AppState.connectorHealthService.matrix`.
struct ConnectorHealthRailCard: View {
    @EnvironmentObject var state: AppState

    /// Display labels for the 8 tracked connectors. Order must match
    /// `ConnectorHealthService.defaultConnectors`.
    private static let labels: [(String, String)] = [
        ("mcp:plugin_slack_slack",          "Slack"),
        ("mcp:plugin_linear_linear",        "Linear"),
        ("mcp:claude_ai_Gmail",             "Gmail"),
        ("mcp:claude_ai_Google_Calendar",   "Calendar"),
        ("mcp:claude_ai_Granola",           "Granola"),
        ("mcp:claude_ai_Google_Drive",      "Drive"),
        ("github",                          "GitHub"),
        ("mcp:claude-in-chrome",            "Chrome")
    ]

    var body: some View {
        let matrix = state.connectorHealthService.matrix
        let fallbackReason = state.connectorHealthService.rosterFallbackReason
        VStack(alignment: .leading, spacing: 0) {
            if let reason = fallbackReason {
                rosterFallbackBanner(reason: reason)
                    .padding(.bottom, 10)
            }
            RailCardHeader(title: "Connector health")
            if matrix.sessionsNewestFirst.isEmpty {
                Text("No scheduled runs have produced connector data yet.")
                    .font(DS.mono(11.5))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.vertical, 12)
            } else {
                grid(matrix: matrix)
            }
        }
        .editorialCard(padding: 16)
    }

    // MARK: - Roster fallback banner

    /// Yellow advisory shown above the matrix when the connector roster
    /// snapshot couldn't be loaded and the service is running on the
    /// hardcoded fallback list. Surfaces the actionable message verbatim
    /// from `ConnectorHealthService.rosterFallbackReason`.
    private func rosterFallbackBanner(reason: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("⚠")
                .font(DS.mono(12))
                .foregroundStyle(DS.Status.warn)
            Text(reason)
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p2)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 4)
                .fill(DS.Status.warn.opacity(0.12))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 4)
                .strokeBorder(DS.Status.warn.opacity(0.45), lineWidth: 0.5)
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Connector roster fallback warning: \(reason)")
    }

    // MARK: - Grid

    private func grid(matrix: ConnectorHealthMatrix) -> some View {
        let cols = Array(matrix.sessionsNewestFirst.prefix(5))
        return VStack(spacing: 4) {
            HStack(spacing: 4) {
                Text("").frame(width: 70, alignment: .leading)
                ForEach(Array(cols.enumerated()), id: \.offset) { idx, _ in
                    Text("r\(idx + 1)")
                        .font(DS.mono(10))
                        .foregroundStyle(DS.Ink.p4)
                        .frame(width: 22)
                }
                Text("7d")
                    .font(DS.mono(10))
                    .foregroundStyle(DS.Ink.p4)
                    .frame(width: 36, alignment: .trailing)
            }
            ForEach(Self.labels, id: \.0) { (key, label) in
                HStack(spacing: 4) {
                    Text(label)
                        .font(DS.mono(11.5))
                        .foregroundStyle(DS.Ink.p2)
                        .frame(width: 70, alignment: .leading)
                    ForEach(cols, id: \.id) { session in
                        cellView(matrix.cell(connector: key, sessionId: session.id))
                            .frame(width: 22)
                    }
                    Text(rateText(matrix.successRate(connector: key)))
                        .font(DS.mono(10.5))
                        .foregroundStyle(DS.Ink.p3)
                        .frame(width: 36, alignment: .trailing)
                }
            }
        }
    }

    private func cellView(_ cell: ConnectorHealthMatrix.Cell) -> some View {
        switch cell {
        case .ok:      return AnyView(Text("✓").foregroundStyle(DS.Status.ok))
        case .error:   return AnyView(Text("✗").foregroundStyle(DS.Status.err))
        case .partial: return AnyView(Text("!").foregroundStyle(DS.Status.warn))
        case .absent:  return AnyView(Text("·").foregroundStyle(DS.Ink.p4))
        }
    }

    private func rateText(_ rate: Double) -> String {
        "\(Int((rate * 100).rounded()))%"
    }
}
