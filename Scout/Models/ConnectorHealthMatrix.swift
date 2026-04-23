import Foundation

/// Aggregated connector-health view for rendering the rail card.
/// Rows = connectors (in the order passed to init). Columns = sessions
/// (newest first). Cell state is one of four buckets.
struct ConnectorHealthMatrix: Equatable, Sendable {
    struct Session: Equatable, Sendable {
        let id: String
        let mode: String
        let startedAt: Date
    }

    enum Cell: Equatable, Sendable {
        case ok(count: Int)
        case partial(ok: Int, total: Int)
        case error
        case absent
    }

    private struct Tally: Equatable, Sendable {
        var ok: Int = 0
        var total: Int = 0
    }

    let connectors: [String]
    let sessionsNewestFirst: [Session]
    private let cells: [String: [String: Cell]]   // connector → sessionId → Cell
    private let totals: [String: Tally]           // connector → rolled-up

    init(calls: [ConnectorCall], connectors: [String]) {
        self.connectors = connectors

        // Group calls by session → mode + startedAt (min ts).
        var bySession: [String: [ConnectorCall]] = [:]
        for c in calls { bySession[c.sessionId, default: []].append(c) }
        let sessions: [Session] = bySession.map { (sid, arr) in
            let startedAt = arr.map(\.ts).min() ?? Date.distantPast
            let mode = arr.first?.mode ?? "?"
            return Session(id: sid, mode: mode, startedAt: startedAt)
        }.sorted { $0.startedAt > $1.startedAt }
        self.sessionsNewestFirst = sessions

        // Build cells + rolled-up per-connector totals.
        var cells: [String: [String: Cell]] = [:]
        var tally: [String: Tally] = [:]
        for connector in connectors {
            var row: [String: Cell] = [:]
            for session in sessions {
                let inSession = (bySession[session.id] ?? [])
                    .filter { $0.connector == connector }
                let total = inSession.count
                let ok = inSession.filter { !$0.error }.count
                row[session.id] = Self.bucket(ok: ok, total: total)
                var t = tally[connector, default: Tally()]
                t.ok += ok
                t.total += total
                tally[connector] = t
            }
            cells[connector] = row
        }
        self.cells = cells
        self.totals = tally
    }

    func cell(connector: String, sessionId: String) -> Cell {
        cells[connector]?[sessionId] ?? .absent
    }

    /// Success rate across all sessions (0.0–1.0). Returns 0 for a
    /// never-called connector.
    func successRate(connector: String) -> Double {
        guard let t = totals[connector], t.total > 0 else { return 0.0 }
        return Double(t.ok) / Double(t.total)
    }

    private static func bucket(ok: Int, total: Int) -> Cell {
        switch (ok, total) {
        case (0, 0):              return .absent
        case (let o, let t) where o == t: return .ok(count: t)
        case (0, _):              return .error
        default:                  return .partial(ok: ok, total: total)
        }
    }
}
