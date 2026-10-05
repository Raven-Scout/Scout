import Testing
@testable import Scout

private let sample = BudgetSettings(
    configPath: "/Users/alex/Scout/scout-config.yaml",
    source: .vault,
    dailyUSD: 200,
    windowHours: 3,
    skipAtPct: 90,
    failureBackoffMinutes: 30,
    windowBudgetUSD: 25.0,
    skipThresholdUSD: 22.5
)

@Suite("BudgetDraft")
struct BudgetDraftTests {

    @Test("seeds from settings without trailing zeros")
    func seeds() {
        let draft = BudgetDraft(from: sample)
        #expect(draft.dailyUSD == "200")
        #expect(draft.windowHours == "3")
        #expect(draft.skipAtPct == "90")
        #expect(draft.failureBackoffMinutes == "30")
    }

    @Test("parses a complete valid draft")
    func parsesValid() throws {
        let parsed = try #require(BudgetDraft(from: sample).parsed)
        #expect(parsed.daily == 200)
        #expect(parsed.hours == 3)
        #expect(parsed.pct == 90)
        #expect(parsed.backoff == 30)
    }

    @Test("keeps a fractional dollar amount")
    func parsesFraction() throws {
        var draft = BudgetDraft(from: sample)
        draft.dailyUSD = "12.50"
        #expect(try #require(draft.parsed).daily == 12.5)
        #expect(draft.validationMessage == nil)
    }

    @Test("non-numeric input does not parse")
    func rejectsNonNumeric() {
        var draft = BudgetDraft(from: sample)
        draft.dailyUSD = "lots"
        #expect(draft.parsed == nil)
        #expect(draft.validationMessage != nil)
    }

    @Test("an empty field does not parse")
    func rejectsEmpty() {
        var draft = BudgetDraft(from: sample)
        draft.windowHours = ""
        #expect(draft.parsed == nil)
    }

    /// The engine rejects these on `budget set`, so catching them here keeps a
    /// pointless subprocess and a raw stderr string away from the user.
    @Test("mirrors the engine's bounds")
    func mirrorsEngineBounds() throws {
        var draft = BudgetDraft(from: sample)
        draft.windowHours = "0"
        #expect(draft.parsed == nil)
        #expect(try #require(draft.validationMessage).contains("window"))

        draft = BudgetDraft(from: sample)
        draft.skipAtPct = "101"
        #expect(draft.parsed == nil)

        draft = BudgetDraft(from: sample)
        draft.dailyUSD = "-1"
        #expect(draft.parsed == nil)

        draft = BudgetDraft(from: sample)
        draft.failureBackoffMinutes = "-1"
        #expect(draft.parsed == nil)
    }

    @Test("a zero daily budget is a real choice, not an error")
    func zeroDailyAllowed() {
        var draft = BudgetDraft(from: sample)
        draft.dailyUSD = "0"
        #expect(draft.parsed != nil)
        #expect(draft.validationMessage == nil)
    }

    @Test("100 percent and a 24h window are in range")
    func upperBoundsAllowed() {
        var draft = BudgetDraft(from: sample)
        draft.skipAtPct = "100"
        draft.windowHours = "24"
        #expect(draft.parsed != nil)
    }

    @Test("a draft equal to the loaded settings is not dirty")
    func notDirtyWhenUnchanged() {
        let draft = BudgetDraft(from: sample)
        #expect(draft == BudgetDraft(from: sample))
    }
}
