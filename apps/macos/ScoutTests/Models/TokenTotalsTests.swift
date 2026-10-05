import Testing
import Foundation
@testable import Scout

@Suite("TokenTotals")
struct TokenTotalsTests {
    @Test func sumsTokensAndCost() {
        let entries = [
            makeEntry(model: "claude-opus-4-7", input: 100, output: 20, cacheR: 1000, cacheC: 500, cost: "0.20"),
            makeEntry(model: "claude-sonnet-4-6", input: 50, output: 10, cacheR: 200, cacheC: 0, cost: "0.01")
        ]
        let totals = TokenTotals(entries: entries)

        #expect(totals.inputTokens == 150)
        #expect(totals.outputTokens == 30)
        #expect(totals.cacheReadTokens == 1200)
        #expect(totals.cacheCreationTokens == 500)
        #expect(totals.allTokens == 1880)
        #expect(totals.costUSD == Decimal(string: "0.21"))
    }

    @Test func perModelBreakdownAsFractions() {
        let entries = [
            makeEntry(model: "claude-opus-4-7", input: 90, output: 0, cacheR: 0, cacheC: 0, cost: "0"),
            makeEntry(model: "claude-opus-4-7", input: 90, output: 0, cacheR: 0, cacheC: 0, cost: "0"),
            makeEntry(model: "claude-sonnet-4-6", input: 20, output: 0, cacheR: 0, cacheC: 0, cost: "0")
        ]
        let totals = TokenTotals(entries: entries)
        // 90% opus, 10% sonnet (by all-tokens input-only in this fixture)
        #expect(totals.modelShare(startingWith: "claude-opus") == 0.9)
        #expect(totals.modelShare(startingWith: "claude-sonnet") == 0.1)
        #expect(totals.modelShare(startingWith: "claude-haiku") == 0.0)
    }

    @Test func emptyEntriesProducesZeros() {
        let totals = TokenTotals(entries: [])
        #expect(totals.inputTokens == 0)
        #expect(totals.allTokens == 0)
        #expect(totals.costUSD == 0)
        #expect(totals.modelShare(startingWith: "claude-opus") == 0.0)
    }

    @Test func nilPrimaryModelDoesNotCrashBreakdown() {
        let entries = [
            makeEntry(model: nil, input: 100, output: 0, cacheR: 0, cacheC: 0, cost: "0")
        ]
        let totals = TokenTotals(entries: entries)
        #expect(totals.modelShare(startingWith: "claude-opus") == 0.0)
    }

    // MARK: - helpers

    private func makeEntry(
        model: String?, input: Int, output: Int,
        cacheR: Int, cacheC: Int, cost: String
    ) -> SessionTokenEntry {
        SessionTokenEntry(
            ts: Date(), tsEt: "", sessionId: UUID().uuidString, scoutMode: "manual",
            cwd: "/x", primaryModel: model,
            inputTokens: input, outputTokens: output,
            cacheReadInputTokens: cacheR, cacheCreationInputTokens: cacheC,
            costUSD: Decimal(string: cost)!, numTurns: 1, durationMs: 0, error: nil
        )
    }
}
