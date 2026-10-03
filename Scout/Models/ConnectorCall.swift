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

    /// Two formatters built once, not once per decoded timestamp.
    /// `ISO8601DateFormatter()` construction goes all the way into ICU
    /// (`udat_open`); at ~29k records a fresh formatter per `ts` was 54% of
    /// total parse time and kept a core busy on every log append.
    /// `ISO8601DateFormatter` is documented as thread-safe for formatting and
    /// parsing once configured, and neither instance is mutated after setup.
    private static let plainFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let fractionalFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    /// Tolerant parser — skips corrupt lines silently, matching
    /// `UsageTrackerService.parseFile`.
    static func parseFile(at url: URL) -> [ConnectorCall] {
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { return [] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { dec in
            let c = try dec.singleValueContainer()
            let s = try c.decode(String.self)
            if let d = Self.plainFormatter.date(from: s) { return d }
            if let d = Self.fractionalFormatter.date(from: s) { return d }
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

    /// The `connector-calls-YYYY-MM-DD.jsonl` files whose *day* falls inside
    /// the window, newest last. Selecting by filename means old history is
    /// never read at all — the caller used to parse every file ever written
    /// and then filter the rows by timestamp, which is O(all history) on
    /// every refresh.
    ///
    /// A dated file is kept when its day is on or after the day before
    /// `windowStart`: the names are local-midnight days, so the boundary file
    /// can legitimately hold rows inside the window.
    ///
    /// A file whose stem is *not* a date is always kept. Only a filename that
    /// proves the contents are too old earns a skip — otherwise a naming
    /// change in the hook would silently empty the health matrix instead of
    /// merely costing a parse. Row-level `ts` filtering stays the source of
    /// truth for what lands in the window.
    static func logURLs(in directory: URL, since windowStart: Date) -> [URL] {
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]
        ) else { return [] }

        let cutoff = Calendar.current.startOfDay(
            for: windowStart.addingTimeInterval(-24 * 3600)
        )
        return entries
            .filter { url in
                let name = url.lastPathComponent
                guard name.hasPrefix("connector-calls-"), name.hasSuffix(".jsonl")
                else { return false }
                guard let day = dayFormatter.date(from: String(
                    name.dropFirst("connector-calls-".count).dropLast(".jsonl".count)
                )) else { return true }  // undated — read it, don't guess
                return day >= cutoff
            }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    /// Local-time day parser for the `connector-calls-YYYY-MM-DD.jsonl` stem.
    /// The hook names files from the machine's local date, so this must not
    /// be pinned to UTC.
    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .iso8601)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone.current
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

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
