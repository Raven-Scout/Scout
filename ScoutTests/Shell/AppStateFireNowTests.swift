import Testing
import Foundation
@testable import Scout

@Suite("AppState.fireNow arguments")
struct AppStateFireNowArgumentsTests {
    @Test func absolutePathResolvesWithEmptyPrefixAndNoStrayScoutctl() {
        // #45: when scoutctl resolves to an absolute path, argumentsPrefix is
        // empty and argv must be exactly the subcommand — never a stray
        // "scoutctl" arg (which scoutctl would treat as an unknown command).
        let args = AppState.fireNowArguments(
            argumentsPrefix: [], slotKey: "briefing-morning", bypassBudget: false
        )
        #expect(args == ["schedule", "fire-now", "briefing-morning"])
    }

    @Test func envFallbackPrependsScoutctl() {
        // `/usr/bin/env scoutctl …` fallback path: the prefix carries "scoutctl".
        let args = AppState.fireNowArguments(
            argumentsPrefix: ["scoutctl"], slotKey: "k", bypassBudget: false
        )
        #expect(args == ["scoutctl", "schedule", "fire-now", "k"])
    }

    @Test func bypassBudgetAppendsFlag() {
        let args = AppState.fireNowArguments(
            argumentsPrefix: [], slotKey: "k", bypassBudget: true
        )
        #expect(args == ["schedule", "fire-now", "k", "--bypass-budget"])
    }
}
