import Testing
import Foundation
@testable import Scout

@Suite("DraftInput.extract")
struct DraftInputTests {

    @Test func extractsEachTBDInOrderWithTrimmedPrompt() {
        let body = """
        Ahoj,

        potvrdím termín [TBD: ověřit v kalendáři a potvrdit konkrétní čas — dopoledne / odpoledne.] a cenu [TBD:  doplnit částku ].

        Díky
        """
        let inputs = DraftInput.extract(from: body)
        #expect(inputs.count == 2)
        #expect(inputs[0].prompt == "ověřit v kalendáři a potvrdit konkrétní čas — dopoledne / odpoledne.")
        #expect(inputs[0].placeholder == "[TBD: ověřit v kalendáři a potvrdit konkrétní čas — dopoledne / odpoledne.]")
        #expect(inputs[1].prompt == "doplnit částku")
    }

    @Test func noTBDsYieldsEmpty() {
        #expect(DraftInput.extract(from: "Plain reply, nothing to fill.").isEmpty)
    }

    @Test func identicalMarkersGetDistinctIDs() {
        let body = "[TBD: confirm address] ... [TBD: confirm address]"
        let inputs = DraftInput.extract(from: body)
        #expect(inputs.count == 2)
        #expect(inputs[0].id != inputs[1].id)
    }

    @Test func draftExposesInputsFromBody() {
        let d = ReplyDraft(
            fileURL: URL(fileURLWithPath: "/x/T.md"), tag: "T", channel: .email,
            loopType: "direct-debt", to: "a@b.cz", cc: nil, threadRef: "u",
            subject: "s", status: .draft, created: "2026-06-30", contextAnswerRef: nil,
            bodyMarkdown: "Hi [TBD: pick a date] — thanks", summary: nil, relatedMessages: []
        )
        #expect(d.inputs.count == 1)
        #expect(d.inputs[0].prompt == "pick a date")
    }
}
