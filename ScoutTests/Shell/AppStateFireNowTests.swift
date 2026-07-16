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

@Suite("AppState urgent action count")
struct AppStateUrgentActionCountTests {
    @Test func countsOnlyOpenUnsnoozedEffectiveUrgentTasks() {
        let urgent = section(.urgent, [
            task("Open urgent"),
            task("Done urgent", done: true),
            task("Snoozed urgent", snoozed: Date()),
        ])
        let carriedUrgent = section(.neutral, [task("Carried urgent", snoozedFrom: .urgent)])
        let todo = section(.todo, [task("Ordinary todo")])
        let document = ActionItemsDocument(
            date: Date(),
            title: "Actions",
            preamble: [],
            sections: [urgent, carriedUrgent, todo],
            sourceURL: URL(fileURLWithPath: "/tmp/action-items.md"),
            sourceBytes: 0
        )

        #expect(AppState.urgentOpenCount(in: document) == 2)
    }

    private func task(
        _ subject: String,
        done: Bool = false,
        snoozed: Date? = nil,
        snoozedFrom: ActionSection.Kind? = nil
    ) -> ActionTask {
        ActionTask(
            id: UUID(), lineNumber: 1, done: done, subject: subject, plainSubject: subject,
            body: "", comments: [], deepLinks: [], snoozedUntil: snoozed,
            carriedInFrom: nil, snoozedFromKind: snoozedFrom
        )
    }

    private func section(_ kind: ActionSection.Kind, _ tasks: [ActionTask]) -> ActionSection {
        ActionSection(
            id: UUID(), emoji: "", title: kind.rawValue, kind: kind,
            tasks: tasks, bullets: [], tables: [], subheads: []
        )
    }
}
