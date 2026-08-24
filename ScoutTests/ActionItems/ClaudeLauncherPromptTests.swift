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

    @Test func conciseIncludesOnlySubjectAndBody() {
        let task = makeTask(
            plainSubject: "Prepare release notes",
            body: "Summarize the fixes.",
            comments: [TaskComment(author: "alex", timestamp: "", text: "Include metrics.")],
            deepLinks: [.linear(id: "PROJ-123")]
        )
        let out = ClaudeLauncher.prompt(for: task, format: .concise)
        #expect(out == "Prepare release notes\nSummarize the fixes.")
    }

    @Test func markdownChecklistIncludesStatusContextAndLinks() {
        let task = makeTask(
            plainSubject: "Land PROJ-123",
            body: "Confirm the rollout plan.",
            deepLinks: [
                .githubPR(
                    repo: "example-org/app",
                    number: 42,
                    rawURL: URL(string: "https://github.com/example-org/app/pull/42")!
                ),
            ]
        )
        let out = ClaudeLauncher.prompt(for: task, format: .markdownChecklist)
        #expect(out.contains("- [ ] Land PROJ-123"))
        #expect(out.contains("  Confirm the rollout plan."))
        #expect(out.contains("[PR example-org/app#42](https://github.com/example-org/app/pull/42)"))
    }

    @Test func bulkFullContextHasOneHeadingPerTask() {
        let tasks = [makeTask(plainSubject: "First"), makeTask(plainSubject: "Second")]
        let out = ClaudeLauncher.prompt(for: tasks, format: .fullContext)
        #expect(out.hasPrefix("Help me make progress on these 2 action items:"))
        #expect(out.contains("## 1. First"))
        #expect(out.contains("## 2. Second"))
    }

    @Test func emptyBulkCopyIsEmpty() {
        #expect(ClaudeLauncher.prompt(for: [], format: .fullContext).isEmpty)
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
