import Testing
import Foundation
@testable import Scout

@Suite("Topic board — routing a note to the item's session")
struct BoardPromptSenderTests {
    @Test func routesBySession() {
        let uuid = "0f8fad5b-d9cb-469f-a165-70867728950e"
        #expect(BoardPromptSender.route(for: uuid) == .resume(id: uuid))
        #expect(BoardPromptSender.route(for: "https://claude.ai/code/session_0123") == .open(URL(string: "https://claude.ai/code/session_0123")!))
        #expect(BoardPromptSender.route(for: nil) == .newSession)
        #expect(BoardPromptSender.route(for: "--dangerously-skip-permissions") == .newSession)
    }

    @Test func noteComesBeforeContext() {
        #expect(BoardPromptSender.prompt(note: "  merge it once green \n", context: "Help me…") == "merge it once green\n\nHelp me…")
    }

    @Test func confirmationSaysWhereTheNoteWent() {
        #expect(BoardPromptSender.confirmation(for: .newSession) == "Sent to Claude")
        #expect(BoardPromptSender.confirmation(for: .resume(id: "x")).contains("clipboard"))
    }
}
