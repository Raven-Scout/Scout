import Testing
import Foundation
@testable import Scout

@Suite("Task details — model")
struct TaskDetailModelTests {
    private func task(body: String = "", details: [TaskDetail] = [],
                      comments: [TaskComment] = []) -> ActionTask {
        ActionTask(
            id: UUID(), lineNumber: 1, done: false,
            subject: "**Ship the fix**", plainSubject: "Ship the fix",
            body: body, comments: comments, deepLinks: [], details: details,
            snoozedUntil: nil, carriedInFrom: nil
        )
    }

    @Test func summaryPrefersBody() {
        let t = task(body: "CI is green.", details: [TaskDetail(depth: 0, text: "Waiting on Priya")])
        #expect(t.summary == "CI is green.")
    }

    @Test func summaryFallsBackToFirstLineOfFirstDetail() {
        let t = task(details: [
            TaskDetail(depth: 0, text: "Waiting on Priya\n```\nmake test\n```"),
            TaskDetail(depth: 0, text: "Second point"),
        ])
        #expect(t.summary == "Waiting on Priya")
    }

    @Test func summaryEmptyWhenNothingToShow() {
        #expect(task().summary == "")
    }

    @Test func replacingDetailsKeepsEveryOtherField() {
        let original = ActionTask(
            id: UUID(), lineNumber: 7, done: true,
            subject: "**S**", plainSubject: "S", body: "b",
            comments: [TaskComment(author: "alex", timestamp: "", text: "hi")],
            deepLinks: [.linear(id: "PROJ-1234")],
            details: [TaskDetail(depth: 0, text: "old")],
            snoozedUntil: Date(timeIntervalSince1970: 1_000),
            carriedInFrom: Date(timeIntervalSince1970: 2_000),
            indentLevel: 1, shortPrefix: "DETX", snoozedFromKind: .urgent
        )
        let replaced = original.replacingDetails([TaskDetail(depth: 1, text: "new")])
        #expect(replaced.details == [TaskDetail(depth: 1, text: "new")])
        #expect(replaced == ActionTask(
            id: original.id, lineNumber: 7, done: true,
            subject: "**S**", plainSubject: "S", body: "b",
            comments: original.comments, deepLinks: original.deepLinks,
            details: [TaskDetail(depth: 1, text: "new")],
            snoozedUntil: original.snoozedUntil, carriedInFrom: original.carriedInFrom,
            indentLevel: 1, shortPrefix: "DETX", snoozedFromKind: .urgent
        ))
    }

    @Test func searchMatchesTextThatOnlyAppearsInADetail() {
        let t = task(details: [TaskDetail(depth: 0, text: "Blocked on the tracing job")])
        #expect(t.matchesSearch("tracing"))
        #expect(!t.matchesSearch("billing"))
    }

    @Test func searchStillMatchesSubjectBodyAndComments() {
        let t = task(body: "CI is green.",
                     comments: [TaskComment(author: "sam", timestamp: "", text: "ping Alex")])
        #expect(t.matchesSearch("ship"))
        #expect(t.matchesSearch("green"))
        #expect(t.matchesSearch("ping alex"))
        #expect(t.matchesSearch(""))
    }
}
