import Testing
import Foundation
@testable import Scout

@Suite("DraftInput.extract")
struct DraftInputTests {

    @Test func extractsEachTBDInOrderWithTrimmedPrompt() {
        let body = """
        Hi,

        I'll confirm the slot [TBD: check the calendar and confirm a time — morning / afternoon.] and the price [TBD:  fill in the amount ].

        Thanks
        """
        let inputs = DraftInput.extract(from: body)
        #expect(inputs.count == 2)
        #expect(inputs[0].prompt == "check the calendar and confirm a time — morning / afternoon.")
        #expect(inputs[0].placeholder == "[TBD: check the calendar and confirm a time — morning / afternoon.]")
        #expect(inputs[1].prompt == "fill in the amount")
    }

    @Test func noTBDsYieldsEmpty() {
        #expect(DraftInput.extract(from: "Plain reply, nothing to fill.").isEmpty)
    }

    @Test func identicalMarkersGetDistinctIDs() {
        let body = "[TBD: confirm address] ... [TBD: confirm address]"
        let inputs = DraftInput.extract(from: body)
        #expect(inputs.count == 2)
        #expect(inputs[0].id != inputs[1].id)
        // Occurrence counts within one placeholder text, so the writer can
        // target the right instance.
        #expect(inputs[0].occurrence == 0)
        #expect(inputs[1].occurrence == 1)
    }

    /// Identity must not shift for a marker just because a differently-worded
    /// one earlier in the body was filled — the card keys its typed-value state
    /// by `id`, so a global ordinal stranded what the user had already typed.
    @Test func distinctMarkersKeepTheirIDWhenAnEarlierOneIsFilled() {
        let before = DraftInput.extract(from: "one [TBD: a] two [TBD: b] three")
        let after = DraftInput.extract(from: "one filled-in two [TBD: b] three")
        #expect(before.count == 2)
        #expect(after.count == 1)
        #expect(before[1].id == after[0].id)
    }

    @Test func draftExposesInputsFromBody() {
        let d = ReplyDraft(
            fileURL: URL(fileURLWithPath: "/x/T.md"), tag: "T", channel: .email,
            loopType: "direct-debt", to: "priya@example.com", cc: nil, threadRef: "u",
            subject: "s", status: .draft, created: "2026-06-30", contextAnswerRef: nil,
            bodyMarkdown: "Hi [TBD: pick a date] — thanks", summary: nil, relatedMessages: []
        )
        #expect(d.inputs.count == 1)
        #expect(d.inputs[0].prompt == "pick a date")
    }
}
