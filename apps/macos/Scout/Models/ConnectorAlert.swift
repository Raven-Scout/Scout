import Foundation

/// One alert from the engine's `.scout-logs/connector-alerts.json`.
///
/// The engine (`scout.scripts.connector_health_report`) is authoritative: it
/// rewrites that file on every run with exactly the alerts the latest run
/// raised, so Swift does not duplicate the sustained-state alert logic. The
/// format is documented on `render_alerts_state` in that module, and the
/// shared fixture `ScoutTests/Fixtures/connector-alerts.json` is a byte copy of
/// `plugin/engine/tests/fixtures/contract/connector-alerts.json`.
///
/// `connector-alerts.log` beside it is the human-readable history; the app
/// does not read it.
struct ConnectorAlert: Equatable, Hashable, Sendable {
    enum Level: String, Sendable { case critical = "CRITICAL", warning = "WARNING" }

    /// File name under the logs directory.
    static let fileName = "connector-alerts.json"
    /// The only `schema_version` this parser understands. A different one is
    /// an incompatible engine, and its alerts are dropped rather than guessed at.
    static let schemaVersion = 1

    let ts: Date
    let level: Level
    let connector: String
    let reason: String
    let firstSeen: Date

    /// `connector|level|first_seen_ts` — stable across repeated entries for
    /// the same underlying alert, so acking a CRITICAL doesn't suppress a
    /// *new* CRITICAL that starts tomorrow with a fresh `first_seen_ts`.
    private static let fingerprintDateFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    var fingerprint: String {
        "\(connector)|\(level.rawValue)|\(Self.fingerprintDateFormatter.string(from: firstSeen))"
    }

    static func parseFile(at url: URL) -> [ConnectorAlert] {
        guard let data = try? Data(contentsOf: url) else { return [] }
        return parse(data)
    }

    /// Parses the engine's alert state. A missing or malformed file, or an
    /// unknown `schema_version`, yields no alerts; a malformed entry is
    /// skipped without dropping its siblings.
    static func parse(_ data: Data) -> [ConnectorAlert] {
        guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              (root["schema_version"] as? Int) == schemaVersion,
              let rows = root["alerts"] as? [Any]
        else { return [] }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return rows.compactMap { row -> ConnectorAlert? in
            guard let a = row as? [String: Any],
                  let key = a["connector_key"] as? String, !key.isEmpty,
                  let level = (a["level"] as? String).flatMap(Level.init(rawValue:)),
                  let ts = (a["ts"] as? String).flatMap(f.date(from:)),
                  let firstSeen = (a["first_seen"] as? String).flatMap(f.date(from:))
            else { return nil }
            return ConnectorAlert(
                ts: ts, level: level,
                connector: ConnectorKeyAlias.canonical(key),
                reason: a["reason"] as? String ?? "",
                firstSeen: firstSeen
            )
        }
    }
}
