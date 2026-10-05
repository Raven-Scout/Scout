import Testing
@testable import Scout

@Suite("BudgetGate")
struct BudgetGateTests {

    /// The case that makes the two-step rounding observable. The engine rounds
    /// the window budget to cents BEFORE applying the skip percentage, so
    /// round(round(50*5/24, 2) * 0.8, 2) == 8.34, while the algebraically
    /// equivalent round(50*5/24*0.8, 2) == 8.33. These are the engine's default
    /// knobs, so a collapsed mirror would disagree with `scoutctl budget show`
    /// on first launch for every user.
    @Test("engine defaults reproduce the two-step rounding")
    func engineDefaults() {
        let gate = BudgetGate.derive(dailyUSD: 50, windowHours: 5, skipAtPct: 80)
        #expect(gate.windowBudgetUSD == 10.42)
        #expect(gate.skipThresholdUSD == 8.34)
        #expect(gate.skipThresholdUSD != 8.33)
    }

    @Test("calibrated values where both forms happen to agree")
    func calibrated() {
        let gate = BudgetGate.derive(dailyUSD: 200, windowHours: 3, skipAtPct: 90)
        #expect(gate.windowBudgetUSD == 25.0)
        #expect(gate.skipThresholdUSD == 22.5)
    }

    @Test("a zero budget gates everything")
    func zeroBudget() {
        let gate = BudgetGate.derive(dailyUSD: 0, windowHours: 5, skipAtPct: 80)
        #expect(gate.windowBudgetUSD == 0)
        #expect(gate.skipThresholdUSD == 0)
    }

    @Test("a full-day window equals the daily budget")
    func fullDayWindow() {
        let gate = BudgetGate.derive(dailyUSD: 120, windowHours: 24, skipAtPct: 100)
        #expect(gate.windowBudgetUSD == 120.0)
        #expect(gate.skipThresholdUSD == 120.0)
    }

    @Test("matches() accepts the engine's own numbers")
    func matchesEngineValues() {
        let gate = BudgetGate.derive(dailyUSD: 50, windowHours: 5, skipAtPct: 80)
        #expect(gate.matches(windowBudgetUSD: 10.42, skipThresholdUSD: 8.34))
    }

    @Test("matches() rejects the collapsed-rounding value")
    func rejectsCollapsedValue() {
        let gate = BudgetGate.derive(dailyUSD: 50, windowHours: 5, skipAtPct: 80)
        #expect(!gate.matches(windowBudgetUSD: 10.42, skipThresholdUSD: 8.33))
    }
}
