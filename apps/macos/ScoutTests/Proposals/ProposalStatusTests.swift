import Testing
import Foundation
@testable import Scout

@Suite("ProposalStatus.parse")
struct ProposalStatusTests {

    @Test func classifiesLeadingWordCaseInsensitively() {
        #expect(ProposalStatus.parse("Proposed (awaiting Alex approval)") == .proposed)
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
        // Audit #64: an unknown status stays with the undecided ones, not
        // under Resolved, as an unknown Wishlist status stays open.
        #expect(ProposalStatus.unknown("x").isAwaitingDecision)
        #expect(!ProposalStatus.unknown("x").isResolved)
    }

    @Test func displayNames() {
        #expect(ProposalStatus.proposed.displayName == "Proposed")
        #expect(ProposalStatus.pending(autoApplyDate: "2026-06-13").displayName == "Pending")
        #expect(ProposalStatus.unknown("Deferred").displayName == "Deferred")
    }

    @Test("the rejected pill uses the Decline button's word")
    func rejectedReadsDeclined() {
        #expect(ProposalStatus.rejected.displayName == "Declined")
        #expect(ProposalDecision.decline.actionName.hasPrefix("Decline"))
        // The file still gets the engine's word.
        #expect(ProposalDecision.decline.statusWord == "Rejected")
    }

    private static let calendar: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        c.locale = Locale(identifier: "en_US")
        return c
    }()

    private static func day(_ y: Int, _ m: Int, _ d: Int, hour: Int = 0) -> Date {
        calendar.date(from: DateComponents(year: y, month: m, day: d, hour: hour))!
    }

    @Test("a pending proposal's auto-apply day parses to a date")
    func autoApplyDay() {
        let status = ProposalStatus.parse("Pending (auto-apply after 2026-06-13)")
        #expect(status.autoApplyDay(calendar: Self.calendar) == Self.day(2026, 6, 13))
        #expect(ProposalStatus.pending(autoApplyDate: nil).autoApplyDay(calendar: Self.calendar) == nil)
        #expect(ProposalStatus.proposed.autoApplyDay(calendar: Self.calendar) == nil)
    }

    @Test("a pending card says when it applies by itself (audit #62)")
    func pendingExplanation() throws {
        let status = ProposalStatus.pending(autoApplyDate: "2026-06-13")
        let before = try #require(status.explanation(today: Self.day(2026, 6, 10, hour: 9), calendar: Self.calendar))
        #expect(before.contains("Applies automatically after"))
        #expect(before.contains("2026"))
        #expect(before.contains("decline"))
        // On the day itself the wait hasn't ended yet.
        let onDay = try #require(status.explanation(today: Self.day(2026, 6, 13, hour: 15), calendar: Self.calendar))
        #expect(onDay.contains("Applies automatically after"))
        let after = try #require(status.explanation(today: Self.day(2026, 6, 14, hour: 9), calendar: Self.calendar))
        #expect(after.contains("next dreaming run"))
        // A bare Pending (governance and safety) is applied only after an
        // explicit Approved; it must never read as applying by itself.
        let undated = try #require(ProposalStatus.pending(autoApplyDate: nil).explanation(calendar: Self.calendar))
        #expect(!undated.contains("Applies automatically"))
        #expect(undated.contains("only after you approve"))
    }

    @Test("only an auto-apply clause's date is an auto-apply date")
    func autoApplyDateComesFromTheClause() {
        #expect(ProposalStatus.parse("Pending") == .pending(autoApplyDate: nil))
        #expect(ProposalStatus.parse("Pending (filed 2026-06-01, needs review)") == .pending(autoApplyDate: nil))
        #expect(ProposalStatus.parse("Pending (filed 2026-06-01; auto-apply after 2026-06-04)")
                == .pending(autoApplyDate: "2026-06-04"))
        #expect(ProposalStatus.parse("pending (Auto-apply after: 2026-06-04)") == .pending(autoApplyDate: "2026-06-04"))
        #expect(ProposalStatus.parse("Pending auto-apply after 2026-06-04") == .pending(autoApplyDate: "2026-06-04"))
        #expect(ProposalStatus.parse("Pending, auto-apply after 2026-06-04") == .pending(autoApplyDate: "2026-06-04"))
    }

    @Test("a negated auto-apply clause is not an auto-apply date", arguments: [
        "Pending — do not auto-apply after 2026-06-04",
        "Pending (not auto-apply after 2026-06-04)",
        "Pending (needs approval; never auto-apply after 2026-06-04)",
    ])
    func negatedAutoApplyClause(raw: String) {
        let status = ProposalStatus.parse(raw)
        #expect(status == .pending(autoApplyDate: nil))
        #expect(status.explanation()?.contains("only after you approve") == true)
    }

    @Test("leading emoji and punctuation don't hide the status word", arguments: [
        ("✅ Approved (2026-06-14)", ProposalStatus.approved),
        ("❌ Rejected", .rejected),
        ("**Applied** — 2026-06-02", .applied(date: "2026-06-02")),
        ("⏳ Pending (auto-apply after 2026-06-13)", .pending(autoApplyDate: "2026-06-13")),
    ])
    func stripsLeadingNonLetters(raw: String, expected: ProposalStatus) {
        #expect(ProposalStatus.parse(raw) == expected)
    }

    @Test("settled-without-a-decision statuses are resolved, not waiting on you", arguments: [
        ("Superseded — 2026-06-10 (merged into another proposal)", "Superseded"),
        ("Withdrawn", "Withdrawn"),
        ("obsolete: no longer needed", "Obsolete"),
    ])
    func closedStatuses(raw: String, label: String) throws {
        let status = ProposalStatus.parse(raw)
        guard case .closed = status else {
            Issue.record("\(raw) parsed as \(status)")
            return
        }
        #expect(!status.isAwaitingDecision)
        #expect(!status.hasPrimaryApprove)
        #expect(status.displayName == label)
        #expect(status.explanation() == nil)
    }

    @Test("a partially applied proposal counts as applied")
    func partiallyApplied() {
        #expect(ProposalStatus.parse("Partially Applied") == .applied(date: nil))
        #expect(ProposalStatus.parse("Partially applied — 2026-06-05").isResolved)
    }

    @Test("an unknown status stays visible but gets no primary Approve")
    func unknownHasNoPrimaryApprove() {
        let unknown = ProposalStatus.parse("Deferred to Q3")
        #expect(unknown.isAwaitingDecision)
        #expect(!unknown.hasPrimaryApprove)
        #expect(ProposalStatus.proposed.hasPrimaryApprove)
        #expect(ProposalStatus.pending(autoApplyDate: nil).hasPrimaryApprove)
    }

    @Test("an unknown status explains itself; settled statuses need no line")
    func otherExplanations() throws {
        let unknown = try #require(ProposalStatus.unknown("Deferred to Q3").explanation())
        #expect(unknown.contains("Deferred to Q3"))
        #expect(ProposalStatus.proposed.explanation() == nil)
        #expect(ProposalStatus.approved.explanation() == nil)
        #expect(ProposalStatus.rejected.explanation() == nil)
        #expect(ProposalStatus.applied(date: "2026-06-02").explanation() == nil)
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
