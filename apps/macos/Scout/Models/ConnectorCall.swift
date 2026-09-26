import Foundation

/// One row of `~/Scout/.scout-logs/connector-calls-YYYY-MM-DD.jsonl` —
/// produced by the existing PostToolUse hook at `~/Scout/hooks/connector-log.sh`.
struct ConnectorCall: Codable, Equatable, Hashable, Sendable {
    let ts: Date
    let sessionId: String
    let mode: String
    let tool: String
    let connector: String
    let error: Bool
    let err: String?

    private enum CodingKeys: String, CodingKey {
        case ts, sessionId = "session_id", mode, tool, connector, error, err
    }

    /// Tolerant parser — skips corrupt lines silently, matching
    /// `UsageTrackerService.parseFile`.
    static func parseFile(at url: URL) -> [ConnectorCall] {
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { return [] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { dec in
            let c = try dec.singleValueContainer()
            let s = try c.decode(String.self)
            let f = ISO8601DateFormatter()
            f.formatOptions = [.withInternetDateTime]
            if let d = f.date(from: s) { return d }
            f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let d = f.date(from: s) { return d }
            throw DecodingError.dataCorruptedError(in: c, debugDescription: s)
        }
        var out: [ConnectorCall] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            guard let d = line.data(using: .utf8) else { continue }
            if let call = try? decoder.decode(ConnectorCall.self, from: d) {
                out.append(call.canonicalized())
            }
        }
        return out
    }

    /// Returns a copy with `connector` normalized through `ConnectorKeyAlias`.
    /// Keeps the rest of the system free of rename drift.
    func canonicalized() -> ConnectorCall {
        let canonical = ConnectorKeyAlias.canonical(connector)
        guard canonical != connector else { return self }
        return ConnectorCall(
            ts: ts, sessionId: sessionId, mode: mode, tool: tool,
            connector: canonical, error: error, err: err
        )
    }
}
