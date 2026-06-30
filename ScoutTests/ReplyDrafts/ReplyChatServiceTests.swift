import Testing
import Foundation
@testable import Scout

@Suite("ReplyChatService.buildPrompt")
struct ReplyChatServiceTests {

    private func draft() -> ReplyDraft {
        ReplyDraft(
            fileURL: URL(fileURLWithPath: "/x/S1.md"), tag: "S1", channel: .email,
            loopType: "direct-debt", to: "Lucia <l@slsp.sk>", cc: "Jakub <j@slsp.sk>",
            threadRef: "u", subject: "Re: roles", status: .draft, created: "2026-06-30",
            contextAnswerRef: nil, bodyMarkdown: "Ahoj Lucio, ...",
            summary: "GDPR role per use-case; ball is with us.",
            relatedMessages: [DraftMessage(date: "2026-05-26", sender: "Lucia", text: "tři varianty", id: "0")]
        )
    }

    @Test func promptGroundsInDraftContext() {
        let p = ReplyChatService.buildPrompt(
            draft: draft(),
            history: [ChatMessage(role: .user, text: "What should I emphasize?", id: "u1")]
        )
        #expect(p.contains("GDPR role per use-case"))          // summary
        #expect(p.contains("[2026-05-26] Lucia: tři varianty")) // thread
        #expect(p.contains("To: Lucia <l@slsp.sk>"))
        #expect(p.contains("Cc: Jakub <j@slsp.sk>"))
        #expect(p.contains("Ahoj Lucio, ..."))                 // current reply
        #expect(p.contains("Me: What should I emphasize?"))    // user turn
        #expect(p.contains("Do NOT send anything"))            // safety framing
    }

    @Test func slackDeliveryPromptSendsVerbatimAndExpectsAck() {
        let p = ReplyChatService.deliveryPrompt(.slackSend, draft: draft())
        #expect(p.contains("SEND"))
        #expect(p.contains("Slack"))
        #expect(p.contains(draft().threadRef))
        #expect(p.contains(draft().bodyMarkdown))
        #expect(p.contains("OK SENT"))
    }

    @Test func gmailDeliveryPromptCreatesDraftNeverSends() {
        let p = ReplyChatService.deliveryPrompt(.gmailDraft, draft: draft())
        #expect(p.contains("CREATE A DRAFT"))
        #expect(p.contains("do NOT send") || p.contains("Never send"))
        #expect(p.contains("Cc: Jakub <j@slsp.sk>"))
        #expect(p.contains("OK DRAFT"))
    }

    @Test func errorTurnsAreOmittedFromConversation() {
        let p = ReplyChatService.buildPrompt(
            draft: draft(),
            history: [
                ChatMessage(role: .user, text: "hi", id: "1"),
                ChatMessage(role: .error, text: "claude exited 1", id: "2"),
            ]
        )
        #expect(p.contains("Me: hi"))
        #expect(!p.contains("claude exited 1"))
    }
}
