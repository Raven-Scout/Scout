import Testing
import Foundation
@testable import Scout

/// #356 regression guard: a bold subject with a second bold span nested inside
/// it (`**… **inner** …**`) must still parse as one open task. It was first
/// blamed for an item missing from Action Items; the parser turned out to keep
/// it, so this pins that down.
@Suite("ActionItemsParser — nested bold in a subject")
struct NestedBoldSubjectTests {
    private let line = "- [ ] [#ZQXV10] 🔴 **Ask [[people/alex-example|Alex]] whether "
        + "`example-org/demo#1234` is ready — it shipped **Oct 1, 9:00 AM ET (2 days ago)**, "
        + "and the next step is blocked by a review.** _(✏️ re-headlined)_"

    private func parse() throws -> [ActionTask] {
        let text = """
        # T

        ## 🔴 Urgent

        \(line)
          - ✏️ **Re-headlined 2026-09-01 (dreaming)** — the old headline narrated.
          - alex: still waiting on this

        ## 🟡 To Do

        - [ ] [#QZDEMO42] **Next task** — after it
        """
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-04-20.md")
        let doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
        return doc.sections.flatMap(\.tasks)
    }

    @Test func keepsTheTaskAndTheOneAfterIt() throws {
        let tasks = try parse()
        #expect(tasks.map(\.shortPrefix) == ["ZQXV10", "QZDEMO42"])
        #expect(tasks.first?.done == false)
        #expect(tasks.first?.snoozedUntil == nil)
    }

    @Test func wholeLineIsTheSubjectAndSubBulletsAttach() throws {
        let t = try #require(try parse().first)
        // The only ` — ` sits inside the outer bold span, so there is no body.
        #expect(t.body == "")
        #expect(t.subject.hasPrefix("🔴 **Ask "))
        #expect(t.subject.hasSuffix("_(✏️ re-headlined)_"))
        #expect(t.details.count == 1)
        #expect(t.comments.map(\.author) == ["alex"])
    }

    @Test func plainSubjectDropsEveryBoldMarker() throws {
        let t = try #require(try parse().first)
        #expect(!t.plainSubject.contains("**"))
        #expect(t.plainSubject.contains("it shipped Oct 1, 9:00 AM ET (2 days ago), and the next step"))
    }
}
