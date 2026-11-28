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
        // The services this reads; AppState re-publishes none of their changes.
        // `DayTick`: a column labelled "8 AM" today reads "Tue" tomorrow.
        Observing(state.connectorHealthService, DayTick.shared) { observedBody }
    }

    @ViewBuilder private var observedBody: some View {
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
                    .font(DS.font(.caption))
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
        // Same words as the Signals card; the raw reason is behind Details.
        ProblemView(problem: .connectorRosterMissing(details: reason))
    }

    // MARK: - Grid

    /// Each column is a recent run, headed by when it started ("8 AM" today,
    /// "Tue" before), not "r1…r5"; only connectors those runs called get a
    /// row, and the rest are counted underneath (UI audit R10, #29).
    private func grid(matrix: ConnectorHealthMatrix) -> some View {
        let visibleSessions = Array(matrix.sessionsNewestFirst.prefix(Self.visibleSessionCount))
        let used = Self.usedConnectors(matrix: matrix, in: visibleSessions)
        let unused = matrix.connectors.count - used.count
        let now = Date()
        return VStack(alignment: .leading, spacing: 4) {
            Text("Last \(visibleSessions.count) run\(visibleSessions.count == 1 ? "" : "s"), newest first")
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p3)
                .padding(.bottom, 2)
            HStack(spacing: 4) {
                Text("").frame(width: 80, alignment: .leading)
                ForEach(Array(visibleSessions.enumerated()), id: \.offset) { idx, session in
                    Text(Self.columnLabel(session.startedAt, now: now))
                        .font(DS.font(.micro, .mono))
                        .foregroundStyle(DS.Ink.p4)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .frame(width: Self.cellWidth)
                        .help(sessionHeaderTooltip(session: session, index: idx))
                        .accessibilityLabel(sessionHeaderTooltip(session: session, index: idx))
                }
                Text("Worked")
                    .font(DS.font(.micro))
                    .foregroundStyle(DS.Ink.p4)
                    .frame(width: 44, alignment: .trailing)
                    .help("How often this connector worked in these runs, counting only the runs that used it.")
            }
            ForEach(used, id: \.self) { key in
                row(matrix: matrix, key: key, visibleSessions: visibleSessions)
            }
            if used.isEmpty {
                Text("These runs didn't use any connectors.")
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.top, 4)
            } else if unused > 0 {
                Text("\(unused) more connector\(unused == 1 ? "" : "s") weren't used in these runs.")
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.top, 4)
            }
        }
    }

    static let cellWidth: CGFloat = 26

    /// Connectors any of `sessions` called, in roster order.
    static func usedConnectors(matrix: ConnectorHealthMatrix,
                                           in sessions: [ConnectorHealthMatrix.Session]) -> [String] {
        matrix.connectors.filter { key in
            sessions.contains { matrix.cell(connector: key, sessionId: $0.id) != .absent }
        }
    }

    /// A run column's heading: its start hour today ("8 AM"), its weekday
    /// within the week ("Tue"), its date before that ("11/3").
    static func columnLabel(_ date: Date, now: Date, calendar: Calendar = .current) -> String {
        if calendar.isDate(date, inSameDayAs: now) {
            return date.formatted(.dateTime.hour(.defaultDigits(amPM: .abbreviated)))
        }
        if let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: date),
                                              to: calendar.startOfDay(for: now)).day, days < 7 {
            return date.formatted(.dateTime.weekday(.abbreviated))
        }
        return date.formatted(.dateTime.month(.defaultDigits).day())
    }

    private func row(
        matrix: ConnectorHealthMatrix,
        key: String,
        visibleSessions: [ConnectorHealthMatrix.Session]
    ) -> some View {
        HStack(spacing: 4) {
            Text(label(for: key))
                .font(DS.font(.caption, .mono))
                .foregroundStyle(DS.Ink.p2)
                // A long connector name stays on one line instead of making
                // the row many lines tall; the full key is in the tooltip.
                .lineLimit(1)
                .truncationMode(.middle)
                .frame(width: 80, alignment: .leading)
                .help(key)
            ForEach(visibleSessions, id: \.id) { session in
                let cell = matrix.cell(connector: key, sessionId: session.id)
                cellView(cell)
                    .frame(width: Self.cellWidth)
                    .help(cellTooltip(label: label(for: key), session: session, cell: cell))
                    .accessibilityLabel(cellTooltip(label: label(for: key), session: session, cell: cell))
            }
            Text(rateText(matrix.visibleHealthRate(connector: key, in: visibleSessions)))
                .font(DS.font(.caption2, .mono))
                .foregroundStyle(DS.Ink.p3)
                .frame(width: 44, alignment: .trailing)
        }
    }

    @ViewBuilder
    private func cellView(_ cell: ConnectorHealthMatrix.Cell) -> some View {
        switch cell {
        case .ok:      Image(systemName: "checkmark").imageScale(.small).foregroundStyle(DS.Status.ok)
        case .error:   Image(systemName: "xmark").imageScale(.small).foregroundStyle(DS.Status.err)
        case .partial: Image(systemName: "exclamationmark").imageScale(.small).foregroundStyle(DS.Status.warn)
        case .absent:  Image(systemName: "minus").imageScale(.small).foregroundStyle(DS.Ink.p4)
        }
    }

    private func rateText(_ rate: Double?) -> String {
        guard let r = rate else { return "—" }
        return "\(Int((r * 100).rounded()))%"
    }

    // MARK: - Labels

    /// Pretty label for a roster key; see `DisplayName.connector`.
    private func label(for key: String) -> String {
        DisplayName.connector(key)
    }

    // MARK: - Tooltips

    private func sessionHeaderTooltip(session: ConnectorHealthMatrix.Session, index: Int) -> String {
        let when = session.startedAt.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day().hour().minute())
        let name = DisplayName.ledgerRun(slot: session.mode, mode: nil, startedAt: session.startedAt) ?? DisplayName.slotKey(session.mode)
        return "\(name), \(when)"
    }

    private func cellTooltip(
        label: String,
        session: ConnectorHealthMatrix.Session,
        cell: ConnectorHealthMatrix.Cell
    ) -> String {
        let stamp = session.startedAt.formatted(.dateTime.month(.abbreviated).day().hour().minute())
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
