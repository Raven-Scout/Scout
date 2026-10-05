import Foundation

/// One row of `.scout-logs/session-tokens.jsonl`, written by
/// `~/Scout/scripts/sum-session-tokens.sh` as a Stop hook.
struct SessionTokenEntry: Codable, Equatable, Hashable, Sendable {
    let ts: Date
    let tsEt: String
    let sessionId: String
    let scoutMode: String
    let cwd: String
    let primaryModel: String?
    let inputTokens: Int
    let outputTokens: Int
    let cacheReadInputTokens: Int
    let cacheCreationInputTokens: Int
    let costUSD: Decimal
    let numTurns: Int
    let durationMs: Int
    let error: String?

    private enum CodingKeys: String, CodingKey {
        case ts, tsEt = "ts_et", sessionId = "session_id", scoutMode = "scout_mode"
        case cwd, primaryModel = "primary_model"
        case inputTokens = "input_tokens", outputTokens = "output_tokens"
        case cacheReadInputTokens = "cache_read_input_tokens"
        case cacheCreationInputTokens = "cache_creation_input_tokens"
        case costUSD = "cost_usd", numTurns = "num_turns"
        case durationMs = "duration_ms", error
    }

    /// Decoder configured to accept the ISO8601 `ts` string emitted by the
    /// shell script (with or without fractional seconds — mirrors
    /// `UsageTrackerService.parseFile`).
    static func makeDecoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { dec in
            let c = try dec.singleValueContainer()
            let s = try c.decode(String.self)
            if let d = SessionTokenTimestampFormatters.plain.date(from: s) { return d }
            if let d = SessionTokenTimestampFormatters.fractional.date(from: s) { return d }
            throw DecodingError.dataCorruptedError(
                in: c,
                debugDescription: "unparseable ts: \(s)"
            )
        }
        return d
    }
}

/// Two formatters built once, not once per decoded timestamp — the same
/// defect #117 fixed in `UsageTrackerService`. `ISO8601DateFormatter()`
/// construction goes all the way into ICU. The old code retried by mutating
/// `formatOptions` in place, which is why it could not simply be hoisted;
/// two separately-configured instances, never mutated after setup, can be
/// shared (`ISO8601DateFormatter` is documented thread-safe once configured).
private enum SessionTokenTimestampFormatters {
    nonisolated(unsafe) static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    nonisolated(unsafe) static let fractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
}
