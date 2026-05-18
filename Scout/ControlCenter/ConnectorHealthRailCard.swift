import SwiftUI

/// Matrix of connectors × last-5 sessions, with a visible-window health
/// column. Reads `AppState.connectorHealthService.matrix`.
///
/// CC-7 changes:
///   1. **Roster-derived labels**: rows are now generated from
///      `matrix.connectors` so the rail can't silently desync from the
///      service's canonical key list (the bug that hid Slack + Linear
///      behind stale `mcp:plugin_*` keys).
///   2. **Visible-window rate**: the right column reads
///      `visibleHealthRate` — the success rate across the cells the user
///      can actually see, not the full 14-day macro window. Resolves the
///      "100% on the right but the chart shows half blanks" mismatch.
///   3. **Cell tooltips**: every cell has a SwiftUI `.help` showing
///      connector + session timestamp + status so you can interrogate any
///      square instead of guessing.
struct ConnectorHealthRailCard: View {
    @EnvironmentObject var state: AppState

    /// Number of recent sessions to display as cells in the matrix.
    private static let visibleSessionCount = 5

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
    /// hardcoded fallback list.
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
        let visibleSessions = Array(matrix.sessionsNewestFirst.prefix(Self.visibleSessionCount))
        return VStack(spacing: 4) {
            HStack(spacing: 4) {
                Text("").frame(width: 80, alignment: .leading)
                ForEach(Array(visibleSessions.enumerated()), id: \.offset) { idx, session in
                    Text("r\(idx + 1)")
                        .font(DS.mono(10))
                        .foregroundStyle(DS.Ink.p4)
                        .frame(width: 22)
                        .help(sessionHeaderTooltip(session: session, index: idx))
                }
                Text("✓%")
                    .font(DS.mono(10))
                    .foregroundStyle(DS.Ink.p4)
                    .frame(width: 40, alignment: .trailing)
                    .help("Health rate across the visible sessions where this connector was actually called.")
            }
            ForEach(matrix.connectors, id: \.self) { key in
                row(matrix: matrix, key: key, visibleSessions: visibleSessions)
            }
        }
    }

    private func row(
        matrix: ConnectorHealthMatrix,
        key: String,
        visibleSessions: [ConnectorHealthMatrix.Session]
    ) -> some View {
        HStack(spacing: 4) {
            Text(label(for: key))
                .font(DS.mono(11.5))
                .foregroundStyle(DS.Ink.p2)
                .frame(width: 80, alignment: .leading)
                .help(key)
            ForEach(visibleSessions, id: \.id) { session in
                let cell = matrix.cell(connector: key, sessionId: session.id)
                cellView(cell)
                    .frame(width: 22)
                    .help(cellTooltip(label: label(for: key), session: session, cell: cell))
            }
            Text(rateText(matrix.visibleHealthRate(connector: key, in: visibleSessions)))
                .font(DS.mono(10.5))
                .foregroundStyle(DS.Ink.p3)
                .frame(width: 40, alignment: .trailing)
        }
    }

    @ViewBuilder
    private func cellView(_ cell: ConnectorHealthMatrix.Cell) -> some View {
        switch cell {
        case .ok:      Text("✓").foregroundStyle(DS.Status.ok)
        case .error:   Text("✗").foregroundStyle(DS.Status.err)
        case .partial: Text("!").foregroundStyle(DS.Status.warn)
        case .absent:  Text("·").foregroundStyle(DS.Ink.p4)
        }
    }

    private func rateText(_ rate: Double?) -> String {
        guard let r = rate else { return "—" }
        return "\(Int((r * 100).rounded()))%"
    }

    // MARK: - Labels

    /// Pretty label for a roster key. Tries the canonical map first; falls
    /// back to a heuristic so brand-new connectors render readably without
    /// requiring a code change every time the YAML grows.
    private func label(for key: String) -> String {
        if let known = Self.canonicalLabels[key] { return known }
        return Self.heuristicLabel(for: key)
    }

    /// Hand-tuned labels for keys we know about. Source of truth for these
    /// is the scout-plugin connectors.yaml; keep additions in sync there.
    private static let canonicalLabels: [String: String] = [
        "mcp:claude_ai_Slack":           "Slack",
        "mcp:claude_ai_Linear":          "Linear",
        "mcp:claude_ai_Gmail":           "Gmail",
        "mcp:claude_ai_Google_Calendar": "Calendar",
        "mcp:claude_ai_Granola":         "Granola",
        "mcp:claude_ai_Google_Drive":    "Drive",
        "github":                        "GitHub",
        "mcp:claude-in-chrome":          "Chrome",
        "mcp:whatsapp-mcp":              "WhatsApp",
        "notify:telegram":               "Telegram",
        "cc-session":                    "Claude Code"   // CC-7: reserved for upstream scoutctl emitter
    ]

    /// Best-effort prettifier for keys we haven't seen before. Strips MCP
    /// prefixes, replaces underscores with spaces, title-cases.
    static func heuristicLabel(for key: String) -> String {
        let stripped = key
            .replacingOccurrences(of: "mcp:claude_ai_", with: "")
            .replacingOccurrences(of: "mcp:plugin_", with: "")
            .replacingOccurrences(of: "mcp:", with: "")
            .replacingOccurrences(of: "notify:", with: "")
            .replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
        return stripped.split(separator: " ")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }

    // MARK: - Tooltips

    private func sessionHeaderTooltip(session: ConnectorHealthMatrix.Session, index: Int) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "EEE MMM d · HH:mm"
        return "Session \(index + 1): \(session.mode) · \(fmt.string(from: session.startedAt))"
    }

    private func cellTooltip(
        label: String,
        session: ConnectorHealthMatrix.Session,
        cell: ConnectorHealthMatrix.Cell
    ) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "MMM d · HH:mm"
        let stamp = fmt.string(from: session.startedAt)
        let status: String
        switch cell {
        case .ok(let n):
            status = "\(n) call\(n == 1 ? "" : "s") · all OK"
        case .partial(let ok, let total):
            status = "\(ok)/\(total) OK"
        case .error:
            status = "error"
        case .absent:
            status = "not called"
        }
        return "\(label) · \(stamp) · \(status)"
    }
}
