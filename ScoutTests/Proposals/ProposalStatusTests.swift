import Testing
import Foundation
@testable import Scout

@Suite("ProposalStatus.parse")
struct ProposalStatusTests {

    @Test func classifiesLeadingWordCaseInsensitively() {
        #expect(ProposalStatus.parse("Proposed (awaiting Adam approval)") == .proposed)
        #expect(ProposalStatus.parse("approved") == .approved)
        #expect(ProposalStatus.parse("APPROVED (2026-06-14, via Scout app)") == .approved)
        #expect(ProposalStatus.parse("Rejected") == .rejected)
        // "Declined" is treated as rejected.
        #expect(ProposalStatus.parse("Declined — not a fit") == .rejected)
    }

    @Test func extractsDatesFromPendingAndApplied() {
        #expect(ProposalStatus.parse("Pending (auto-apply after 2026-06-13)")
                == .pending(autoApplyDate: "2026-06-13"))
        #expect(ProposalStatus.parse("Applied — 2026-06-02")
                == .applied(date: "2026-06-02"))
        // Pending with no date still classifies.
        #expect(ProposalStatus.parse("Pending") == .pending(autoApplyDate: nil))
    }

    @Test func unknownStatusPreservedVerbatim() {
        #expect(ProposalStatus.parse("Deferred to Q3") == .unknown("Deferred to Q3"))
    }

    @Test func awaitingDecisionFlag() {
        #expect(ProposalStatus.proposed.isAwaitingDecision)
        #expect(ProposalStatus.pending(autoApplyDate: nil).isAwaitingDecision)
        #expect(!ProposalStatus.approved.isAwaitingDecision)
        #expect(!ProposalStatus.rejected.isAwaitingDecision)
        #expect(!ProposalStatus.applied(date: nil).isAwaitingDecision)
        #expect(!ProposalStatus.unknown("x").isAwaitingDecision)
    }

    @Test func displayNames() {
        #expect(ProposalStatus.proposed.displayName == "Proposed")
        #expect(ProposalStatus.pending(autoApplyDate: "2026-06-13").displayName == "Pending")
        #expect(ProposalStatus.unknown("Deferred").displayName == "Deferred")
    }
}

@Suite("ProposalBodyBlock.blocks")
struct ProposalBodyBlockTests {

    @Test func splitsProseAndCodePreservingOrder() {
        let body = """
        **Problem.** Something went wrong.

        Here is a fix:

        ```bash
        echo hello
        echo world
        ```

        That should do it.
        """
        let blocks = ProposalBodyBlock.blocks(from: body)
        #expect(blocks.count == 4)
        #expect(blocks[0] == .prose("**Problem.** Something went wrong."))
        #expect(blocks[1] == .prose("Here is a fix:"))
        #expect(blocks[2] == .code(language: "bash", code: "echo hello\necho world"))
        #expect(blocks[3] == .prose("That should do it."))
    }

    @Test func plainProseIsOneBlock() {
        let blocks = ProposalBodyBlock.blocks(from: "Just one paragraph of text.")
        #expect(blocks == [.prose("Just one paragraph of text.")])
    }

    @Test func unterminatedFenceKeepsContentAsCode() {
        let blocks = ProposalBodyBlock.blocks(from: "intro\n\n```\nno closing fence")
        #expect(blocks.contains(.code(language: nil, code: "no closing fence")))
    }
}
