import Testing
import Foundation
@testable import Scout

@Suite("ClaudeLauncher — prompt builder")
struct ClaudeLauncherPromptTests {
    @Test func subjectOnly() {
        let task = makeTask(plainSubject: "Reply to Priya's RFC")
        #expect(ClaudeLauncher.prompt(for: task) == """
        Help me make progress on this action item:

        Reply to Priya's RFC
        """)
    }

    @Test func includesBodyWhenPresent() {
        let task = makeTask(
            plainSubject: "Cut release",
            body: "Blocking the mobile team — they want the tag by EOD."
        )
        #expect(ClaudeLauncher.prompt(for: task).contains(
            "Blocking the mobile team — they want the tag by EOD."
        ))
    }

    @Test func includesPriorComments() {
        let task = makeTask(
            plainSubject: "Investigate pager storm",
            comments: [
                TaskComment(author: "alex", timestamp: "2026-04-20 10:00 AM ET",
                            text: "Saw three alerts in ten minutes."),
                TaskComment(author: "priya", timestamp: "",
                            text: "Probably related to the queue drain we shipped."),
            ]
        )
        let out = ClaudeLauncher.prompt(for: task)
        #expect(out.contains("Prior comments:"))
        #expect(out.contains("- alex (2026-04-20 10:00 AM ET): Saw three alerts in ten minutes."))
        #expect(out.contains("- priya: Probably related to the queue drain we shipped."))
    }

    @Test func includesDeepLinks() {
        let task = makeTask(
            plainSubject: "Land PROJ-123",
            deepLinks: [
                .linear(id: "PROJ-123"),
                .githubPR(
                    repo: "acme/app",
                    number: 42,
                    rawURL: URL(string: "https://github.com/acme/app/pull/42")!
                ),
            ]
        )
        let out = ClaudeLauncher.prompt(for: task)
        #expect(out.contains("Links:"))
        #expect(out.contains("- PR acme/app#42: https://github.com/acme/app/pull/42"))
        // Linear URL depends on user's workspace — just assert the label line exists.
        #expect(out.contains("- Linear PROJ-123:"))
    }

    @Test func skipsEmptySections() {
        let task = makeTask(plainSubject: "Bare task")
        let out = ClaudeLauncher.prompt(for: task)
        #expect(!out.contains("Prior comments:"))
        #expect(!out.contains("Links:"))
    }

    private func makeTask(
        plainSubject: String,
        body: String = "",
        comments: [TaskComment] = [],
        deepLinks: [TaskDeepLink] = []
    ) -> ActionTask {
        ActionTask(
            id: UUID(),
            lineNumber: 1,
            done: false,
            subject: plainSubject,
            plainSubject: plainSubject,
            body: body,
            comments: comments,
            deepLinks: deepLinks,
            snoozedUntil: nil,
            carriedInFrom: nil
        )
    }
}
