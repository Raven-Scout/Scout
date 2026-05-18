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
    /// never-called connector. Kept for callers that want the macro view;
    /// the rail card now uses `visibleHealthRate` so the % matches the
    /// chart cells the user can actually see.
    func successRate(connector: String) -> Double {
        guard let t = totals[connector], t.total > 0 else { return 0.0 }
        return Double(t.ok) / Double(t.total)
    }

    /// Health rate across a specific set of visible sessions (typically the
    /// last 5 shown in the rail card). Returns nil when the connector wasn't
    /// called in any visible session — distinct from "called and failed", so
    /// the UI can render "—" instead of a misleading 0%.
    ///
    /// Definition: of the visible sessions where the connector *was* called,
    /// what fraction succeeded fully? `.absent` cells (connector not invoked)
    /// are excluded from both numerator and denominator — they're neutral,
    /// not failures. This makes the % match what the user sees in the chart:
    /// 3 ok / 1 error / 1 absent reads as 3/4 = 75%, not 60% (which would
    /// punish absence) and not 100% (which would over-credit).
    ///
    /// CC-7: fixes the long-standing "100% on the right but the chart isn't
    /// 100%" mismatch where the old rate spanned the full 14-day window.
    func visibleHealthRate(connector: String, in sessions: [Session]) -> Double? {
        var called = 0
        var ok = 0
        for s in sessions {
            switch cell(connector: connector, sessionId: s.id) {
            case .ok:        called += 1; ok += 1
            case .partial,
                 .error:     called += 1
            case .absent:    break
            }
        }
        return called == 0 ? nil : Double(ok) / Double(called)
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
