import Testing
import Foundation
@testable import Scout

private let fullFixture = """
---
tag: NAHSEND
channel: email
loop_type: direct-debt
to: "Jan Novák <jan@firma.cz>"
cc: "Petra Malá <petra@firma.cz>"
thread_ref: "https://mail.google.com/mail/u/0/#inbox/abc123"
subject: "Re: Rozpočet Q3"
status: draft
created: 2026-06-29
context_answer_ref: ""
---

Ahoj Jane,

posílám čísla k Q3 rozpočtu. [TBD: doplnit finální částku]

Měj se,
Vojta
"""

@Suite("ReplyDraftsParser")
struct ReplyDraftsParserTests {

    @Test func parsesAllFrontmatterFields() throws {
        let url = URL(fileURLWithPath: "/x/drafts/NAHSEND.md")
        let d = try #require(ReplyDraftsParser.parseFile(contents: fullFixture, fileURL: url))
        #expect(d.tag == "NAHSEND")
        #expect(d.channel == .email)
        #expect(d.loopType == "direct-debt")
        #expect(d.to == "Jan Novák <jan@firma.cz>")
        #expect(d.cc == "Petra Malá <petra@firma.cz>")
        #expect(d.threadRef == "https://mail.google.com/mail/u/0/#inbox/abc123")
        #expect(d.subject == "Re: Rozpočet Q3")
        #expect(d.status == .draft)
        #expect(d.created == "2026-06-29")
        // Empty quoted value → nil.
        #expect(d.contextAnswerRef == nil)
        #expect(d.bodyMarkdown.hasPrefix("Ahoj Jane,"))
        #expect(d.bodyMarkdown.contains("[TBD: doplnit finální částku]"))
        #expect(d.showsSubject)
    }

    @Test func contextBlockIsSplitFromSendableBody() throws {
        let text = """
        ---
        tag: CTX1
        channel: email
        to: "a@b.cz"
        thread_ref: "u"
        status: draft
        created: 2026-06-30
        ---

        Ahoj, posílám odpověď.

        <!-- scout:context -->
        ## Summary

        Lucia se ptá na možnosti granulárních rolí; míč je na tobě.

        ## Thread

        - [2026-05-26] Lucia Hallonová: shrnula tři varianty řešení
        - [2026-05-22] Vojta (you): feature není na roadmapě
        """
        let d = try #require(ReplyDraftsParser.parseFile(
            contents: text, fileURL: URL(fileURLWithPath: "/x/CTX1.md")))
        // Sendable body excludes the context block (Copy stays clean).
        #expect(d.bodyMarkdown == "Ahoj, posílám odpověď.")
        #expect(!d.bodyMarkdown.contains("scout:context"))
        #expect(!d.bodyMarkdown.contains("Summary"))
        // Summary parsed.
        #expect(d.summary == "Lucia se ptá na možnosti granulárních rolí; míč je na tobě.")
        // Messages parsed.
        #expect(d.relatedMessages.count == 2)
        #expect(d.relatedMessages[0].date == "2026-05-26")
        #expect(d.relatedMessages[0].sender == "Lucia Hallonová")
        #expect(d.relatedMessages[0].text == "shrnula tři varianty řešení")
        #expect(d.relatedMessages[1].sender == "Vojta (you)")
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
        thread_ref: "https://slack.com/archives/C1/p123"
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
        to: "Petra <petra@x.cz>"
        thread_ref: "https://mail/thread/1"
        subject: "Re: termín"
        status: draft
        created: 2026-06-29
        context_answer_ref: "https://slack.com/archives/C2/p999"
        ---

        Ahoj Petro, ptal jsem se a termín je 15. července.
        """
        let url = URL(fileURLWithPath: "/x/drafts/QBACK.md")
        let d = try #require(ReplyDraftsParser.parseFile(contents: text, fileURL: url))
        #expect(d.loopType == "promise-answered")
        #expect(d.contextAnswerRef == "https://slack.com/archives/C2/p999")
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
