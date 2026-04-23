import Foundation

/// Aggregated token/cost totals over a set of `SessionTokenEntry` rows.
///
/// Used by `UsageRailCard` for the today/week rollups and the
/// "opus X% · sonnet Y%" line. All fields are computed at init time so
/// the view can `@MainActor`-safely read them without worrying about
/// sums drifting on re-render.
struct TokenTotals: Equatable, Sendable {
    let inputTokens: Int
    let outputTokens: Int
    let cacheReadTokens: Int
    let cacheCreationTokens: Int
    let costUSD: Decimal

    /// Per-primary-model totals, keyed by whatever `primary_model` string
    /// the shell emitted. Used by `modelShare(startingWith:)`.
    private let tokensByModel: [String: Int]
    private let totalAttributedTokens: Int

    init(entries: [SessionTokenEntry]) {
        var input = 0, output = 0, cacheR = 0, cacheC = 0
        var cost = Decimal(0)
        var byModel: [String: Int] = [:]
        for e in entries {
            input  += e.inputTokens
            output += e.outputTokens
            cacheR += e.cacheReadInputTokens
            cacheC += e.cacheCreationInputTokens
            cost   += e.costUSD
            if let m = e.primaryModel {
                let entryAllTokens = e.inputTokens + e.outputTokens
                    + e.cacheReadInputTokens + e.cacheCreationInputTokens
                byModel[m, default: 0] += entryAllTokens
            }
        }
        self.inputTokens = input
        self.outputTokens = output
        self.cacheReadTokens = cacheR
        self.cacheCreationTokens = cacheC
        self.costUSD = cost
        self.tokensByModel = byModel
        self.totalAttributedTokens = byModel.values.reduce(0, +)
    }

    var allTokens: Int {
        inputTokens + outputTokens + cacheReadTokens + cacheCreationTokens
    }

    /// Fraction (0.0–1.0) of attributed tokens whose `primary_model` begins
    /// with the given prefix (e.g. `"claude-opus"` or `"claude-sonnet"`).
    /// Rows with `primary_model == nil` are excluded from both numerator
    /// and denominator.
    func modelShare(startingWith prefix: String) -> Double {
        guard totalAttributedTokens > 0 else { return 0 }
        let matched = tokensByModel
            .filter { $0.key.hasPrefix(prefix) }
            .values
            .reduce(0, +)
        return Double(matched) / Double(totalAttributedTokens)
    }
}
