import Foundation

/// How a period's cost was found, from `scoutctl budget usage --json`.
///
/// `reported` is Claude Code's own figure for every run; `priced` means Scout
/// priced some runs from their token counts; `unpriced` means no run had a
/// measured cost (the tracker's $0 is not a measurement); `partial` mixes
/// priced and unpriced runs; `none` means no runs. An unknown value decodes to
/// `.partial`, the least confident reading, so a newer engine can't make the
/// card claim a cost it doesn't have.
nonisolated enum UsageCostKind: String, Codable, Sendable {
    case reported, priced, unpriced, partial, none

    init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = UsageCostKind(rawValue: raw) ?? .partial
    }
}

nonisolated struct UsageTokens: Codable, Equatable, Sendable {
    let input: Int
    let output: Int
    let cacheRead: Int
    let cacheCreation: Int

    enum CodingKeys: String, CodingKey {
        case input, output
        case cacheRead = "cache_read"
        case cacheCreation = "cache_creation"
    }

    /// Input plus output: the work the model did, without cache traffic.
    var inputOutput: Int { input + output }
    var cache: Int { cacheRead + cacheCreation }
}

/// One period (today, or this week) of Scout's own scheduled runs.
nonisolated struct UsagePeriod: Codable, Equatable, Sendable {
    /// The period's bounds in the vault's timezone, ISO 8601 with an offset.
    let start: String
    let end: String
    let runs: Int
    let runsWithTokens: Int
    let sessions: Int
    let tokens: UsageTokens
    let costUSD: Double
    let costKind: UsageCostKind
    let pricedRuns: Int

    enum CodingKeys: String, CodingKey {
        case start, end, runs
        case runsWithTokens = "runs_with_tokens"
        case sessions, tokens
        case costUSD = "cost_usd"
        case costKind = "cost_kind"
        case pricedRuns = "priced_runs"
    }

    /// When the period ends; nil if the engine's `end` doesn't parse.
    var endDate: Date? { ISO8601DateFormatter().date(from: end) }
    /// When the period starts; nil if the engine's `start` doesn't parse.
    var startDate: Date? { ISO8601DateFormatter().date(from: start) }

    /// Whether `date` falls in this period, the vault's day or week (`[start, end)`);
    /// nil when either bound doesn't parse.
    func contains(_ date: Date) -> Bool? {
        guard let s = startDate, let e = endDate else { return nil }
        return date >= s && date < e
    }

    /// This period while `now` is in it; nil once it is over (or its bounds don't parse).
    func ifCurrent(at now: Date) -> UsagePeriod? { contains(now) == true ? self : nil }
}

/// The `scoutctl budget usage --json` payload (engine:
/// `scout/scripts/usage_summary.py`; contract fixture `usage-summary.json`).
/// It counts finished scheduled runs only — never the owner's own Claude
/// sessions — with each message's tokens counted once.
nonisolated struct UsageSummary: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let scope: String
    let timezone: String
    let generatedAt: String
    let today: UsagePeriod
    let week: UsagePeriod

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case scope, timezone
        case generatedAt = "generated_at"
        case today, week
    }

    static let supportedSchemaVersion = 1
}

/// Display strings for usage figures. Pure, so tests pin every label.
nonisolated enum UsageFormat {
    /// 950 → "950", 2_500 → "2.5K", 12_400 → "12K", 3_400_000 → "3.4M", 2.1e9 → "2.1B", 4e12 → "4.0T".
    /// A unit that hides a scale (the old card's "270000.0M") is never used.
    static func tokens(_ n: Int) -> String {
        let v = Double(n)
        switch n {
        case 1_000_000_000_000...: return String(format: "%.1fT", v / 1_000_000_000_000)
        case 1_000_000_000...: return String(format: "%.1fB", v / 1_000_000_000)
        case 1_000_000...: return String(format: "%.1fM", v / 1_000_000)
        case 10_000...: return String(format: "%.0fK", v / 1_000)
        case 1_000...: return String(format: "%.1fK", v / 1_000)
        default: return "\(n)"
        }
    }

    static func dollars(_ usd: Double) -> String {
        String(format: "$%.2f", usd)
    }

    /// The card's cost line. Says how the figure was found, and never shows an
    /// unmeasured $0 as if it were a measurement.
    static func cost(_ p: UsagePeriod) -> String {
        switch p.costKind {
        case .none: return "no runs yet"
        case .reported: return "\(dollars(p.costUSD)) (Claude Code's figure)"
        case .priced: return "≈\(dollars(p.costUSD)) (some runs estimated from tokens)"
        case .unpriced: return "cost unknown (no run reported one)"
        case .partial:
            return "at least \(dollars(p.costUSD)) (\(p.pricedRuns) of \(p.runs) runs priced)"
        }
    }

    /// A short form for the status bar.
    static func shortCost(_ p: UsagePeriod) -> String {
        switch p.costKind {
        case .none: return "$0.00"
        case .reported: return dollars(p.costUSD)
        case .priced: return "≈\(dollars(p.costUSD))"
        case .unpriced: return "unknown"
        case .partial: return "≥\(dollars(p.costUSD))"
        }
    }

    /// "6 runs finished, tokens from 5" — what the numbers cover.
    static func coverage(_ p: UsagePeriod) -> String {
        let runs = p.runs == 1 ? "1 run finished" : "\(p.runs) runs finished"
        guard p.runs > 0 else { return "no scheduled runs finished yet" }
        if p.runsWithTokens == p.runs { return runs }
        return "\(runs), tokens from \(p.runsWithTokens)"
    }

    static func cacheLine(_ t: UsageTokens) -> String {
        "in \(tokens(t.input)) · out \(tokens(t.output)) · cache read \(tokens(t.cacheRead)) · cache write \(tokens(t.cacheCreation))"
    }
}
