import Testing
import Foundation
@testable import Scout

private let fullFixture = """
---
tag: NAHSEND
channel: email
loop_type: direct-debt
to: "Priya <priya@example.com>"
cc: "Sam <sam@example.com>"
thread_ref: "https://mail.google.com/mail/u/0/#inbox/abc123"
subject: "Re: Q3 budget"
status: draft
created: 2026-06-29
context_answer_ref: ""
---

Hi Priya,

sending over the Q3 budget numbers. [TBD: fill in the final amount]

Best,
Alex
"""

@Suite("ReplyDraftsParser")
struct ReplyDraftsParserTests {

    @Test func parsesAllFrontmatterFields() throws {
        let url = URL(fileURLWithPath: "/x/drafts/NAHSEND.md")
        let d = try #require(ReplyDraftsParser.parseFile(contents: fullFixture, fileURL: url))
        #expect(d.tag == "NAHSEND")
        #expect(d.channel == .email)
        #expect(d.loopType == "direct-debt")
        #expect(d.to == "Priya <priya@example.com>")
        #expect(d.cc == "Sam <sam@example.com>")
        #expect(d.threadRef == "https://mail.google.com/mail/u/0/#inbox/abc123")
        #expect(d.subject == "Re: Q3 budget")
        #expect(d.status == .draft)
        #expect(d.created == "2026-06-29")
        // Empty quoted value → nil.
        #expect(d.contextAnswerRef == nil)
        #expect(d.bodyMarkdown.hasPrefix("Hi Priya,"))
        #expect(d.bodyMarkdown.contains("[TBD: fill in the final amount]"))
        #expect(d.showsSubject)
    }

    @Test func contextBlockIsSplitFromSendableBody() throws {
        let text = """
        ---
        tag: CTX1
        channel: email
        to: "priya@example.com"
        thread_ref: "u"
        status: draft
        created: 2026-06-30
        ---

        Hi — sending my answer.

        <!-- scout:context -->
        ## Summary

        Priya asked about granular role options; the ball is with you.

        ## Thread

        - [2026-05-26] Priya Rivera: summarized three options
        - [2026-05-22] Alex (you): the feature is not on the roadmap
        """
        let d = try #require(ReplyDraftsParser.parseFile(
            contents: text, fileURL: URL(fileURLWithPath: "/x/CTX1.md")))
        // Sendable body excludes the context block (Copy stays clean).
        #expect(d.bodyMarkdown == "Hi — sending my answer.")
        #expect(!d.bodyMarkdown.contains("scout:context"))
        #expect(!d.bodyMarkdown.contains("Summary"))
        // Summary parsed.
        #expect(d.summary == "Priya asked about granular role options; the ball is with you.")
        // Messages parsed.
        #expect(d.relatedMessages.count == 2)
        #expect(d.relatedMessages[0].date == "2026-05-26")
        #expect(d.relatedMessages[0].sender == "Priya Rivera")
        #expect(d.relatedMessages[0].text == "summarized three options")
        #expect(d.relatedMessages[1].sender == "Alex (you)")
    }

    /// The sender/text split must land on the `": "` that starts the text, not
    /// the first bare colon — a sender carrying a time was truncated ("Alex 10"
    /// with text "30: said hi"), and the mangled name flows into the chat prompt.
    @Test func senderWithAColonIsNotTruncated() {
        let context = """
        ## Thread

        - [2026-05-26] Alex 10:30: said hi
        - [2026-05-26] Priya: re: the budget thread
        """
        let messages = ReplyDraftsParser.parseMessages(context)
        #expect(messages.count == 2)
        #expect(messages[0].sender == "Alex 10:30")
        #expect(messages[0].text == "said hi")
        #expect(messages[1].sender == "Priya")
        #expect(messages[1].text == "re: the budget thread")
    }

    @Test func draftWithoutContextHasNoSummaryOrMessages() throws {
        let url = URL(fileURLWithPath: "/x/drafts/NAHSEND.md")
        let d = try #require(ReplyDraftsParser.parseFile(contents: fullFixture, fileURL: url))
        #expect(d.summary == nil)
        #expect(d.relatedMessages.isEmpty)
    }

    @Test func noFrontmatterReturnsNil() {
        // The drafts/README.md doc has no frontmatter and must be skipped.
        let readme = "# Reply Drafts\n\nThis directory holds prepared replies.\n"
        let url = URL(fileURLWithPath: "/x/drafts/README.md")
        #expect(ReplyDraftsParser.parseFile(contents: readme, fileURL: url) == nil)
    }

    @Test func chatChannelOmitsSubject() throws {
        let text = """
        ---
        tag: PINGAL
        channel: slack
        loop_type: direct-debt
        to: "@alex"
        thread_ref: "https://acme-co.slack.com/archives/C0123456789/p1700000000000000"
        status: draft
        created: 2026-06-29
        ---

        Hey Alex, on it — will send the doc by EOD.
        """
        let url = URL(fileURLWithPath: "/x/drafts/PINGAL.md")
        let d = try #require(ReplyDraftsParser.parseFile(contents: text, fileURL: url))
        #expect(d.channel == .slack)
        #expect(d.subject == nil)
        #expect(!d.showsSubject)
        // No cc: line → nil.
        #expect(d.cc == nil)
    }

    @Test func promiseAnsweredCarriesContextRef() throws {
        let text = """
        ---
        tag: QBACK
        channel: email
        loop_type: promise-answered
        to: "Sam <sam@example.com>"
        thread_ref: "https://mail/thread/1"
        subject: "Re: timing"
        status: draft
        created: 2026-06-29
        context_answer_ref: "https://acme-co.slack.com/archives/C0123456789/p1700000000000001"
        ---

        Hi Sam — I checked, and the date is 15 July.
        """
        let url = URL(fileURLWithPath: "/x/drafts/QBACK.md")
        let d = try #require(ReplyDraftsParser.parseFile(contents: text, fileURL: url))
        #expect(d.loopType == "promise-answered")
        #expect(d.contextAnswerRef == "https://acme-co.slack.com/archives/C0123456789/p1700000000000001")
    }

    @Test func missingStatusDefaultsToDraft() throws {
        let text = "---\ntag: T\nchannel: email\nto: x\nthread_ref: y\n---\n\nbody"
        let url = URL(fileURLWithPath: "/x/drafts/T.md")
        let d = try #require(ReplyDraftsParser.parseFile(contents: text, fileURL: url))
        #expect(d.status == .draft)
    }

    @Test func tagFallsBackToFilenameStem() throws {
        let text = "---\nchannel: email\nstatus: draft\nto: x\nthread_ref: y\n---\n\nbody"
        let url = URL(fileURLWithPath: "/x/drafts/FALLBACK.md")
        let d = try #require(ReplyDraftsParser.parseFile(contents: text, fileURL: url))
        #expect(d.tag == "FALLBACK")
    }
}
