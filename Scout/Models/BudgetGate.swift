import Foundation

/// The gate `scoutctl budget check` computes from the four budget knobs: how
/// much of the daily budget a rolling window is allowed, and the spend at which
/// a scheduled session is skipped.
///
/// This mirrors `BudgetConfig.window_budget_usd` / `skip_threshold_usd` in the
/// engine (`scout/scripts/budget_check.py`) so the Settings readout can update
/// as the user types, before anything is written. `BudgetSettingsService` checks
/// every derived value against the engine's own numbers on load, so a drift
/// between the two implementations surfaces instead of misinforming quietly.
///
/// The two-step rounding is load-bearing and must NOT be collapsed into one
/// expression: the engine rounds the window budget to cents first, then applies
/// the percentage to that rounded figure. At the engine defaults the two forms
/// disagree — 8.34 two-step, 8.33 collapsed.
struct BudgetGate: Equatable, Sendable {
    let windowBudgetUSD: Double
    let skipThresholdUSD: Double

    static func derive(dailyUSD: Double, windowHours: Int, skipAtPct: Double) -> BudgetGate {
        let windowBudget = roundedToCents(dailyUSD * Double(windowHours) / 24)
        let skipThreshold = roundedToCents(windowBudget * skipAtPct / 100)
        return BudgetGate(windowBudgetUSD: windowBudget, skipThresholdUSD: skipThreshold)
    }

    /// True when the engine's reported figures equal what we derived, to the
    /// cent. Compared as integer cents rather than with a Double tolerance —
    /// the values are currency, and the 8.34-vs-8.33 disagreement this guards
    /// against is exactly one cent wide.
    func matches(windowBudgetUSD: Double, skipThresholdUSD: Double) -> Bool {
        cents(self.windowBudgetUSD) == cents(windowBudgetUSD)
            && cents(self.skipThresholdUSD) == cents(skipThresholdUSD)
    }

    private static func roundedToCents(_ value: Double) -> Double {
        (value * 100).rounded() / 100
    }

    private func cents(_ value: Double) -> Int {
        Int((value * 100).rounded())
    }
}
