import Foundation

/// One parsed line from `~/Scout/.scout-logs/connector-alerts.log`.
/// The log is shell-authoritative — Swift does not duplicate the sustained-
/// state alert logic in `~/Scout/scripts/connector-health-report.sh`.
struct ConnectorAlert: Equatable, Hashable, Sendable {
    enum Level: String, Sendable { case critical = "CRITICAL", warning = "WARNING" }

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

    /// Parses the pipe-separated log format emitted by
    /// `connector-health-report.sh`. Format per line:
    /// `ts | LEVEL | connector | reason | first_seen=ts`
    static func parseFile(at url: URL) -> [ConnectorAlert] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        var out: [ConnectorAlert] = []
        for raw in text.split(separator: "\n", omittingEmptySubsequences: true) {
            let parts = raw.split(separator: "|").map {
                $0.trimmingCharacters(in: .whitespaces)
            }
            guard parts.count == 5,
                  let ts = f.date(from: parts[0]),
                  let level = Level(rawValue: parts[1]),
                  parts[4].hasPrefix("first_seen="),
                  let firstSeen = f.date(from: String(parts[4].dropFirst("first_seen=".count)))
            else { continue }
            out.append(ConnectorAlert(
                ts: ts, level: level,
                connector: ConnectorKeyAlias.canonical(parts[2]),
                reason: parts[3], firstSeen: firstSeen
            ))
        }
        return out
    }
}
