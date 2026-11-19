import Foundation

/// Lifecycle status of a dreaming proposal, parsed from the `**Status:**` line
/// of a proposal section in `dreaming-proposals.md`.
///
/// The status vocabulary mirrors the scout-plugin feedback-processing spec:
/// a proposal moves `Proposed` / `Pending` → `Approved` or `Rejected`, and a
/// later dreaming run flips an approved (or ripe opt-out `Pending`) proposal to
/// `Applied`, or marks one `Superseded` when another replaces it. The app only ever writes `Approved` / `Rejected` — applying the
/// underlying SKILL.md change stays with the dreaming run.
nonisolated enum ProposalStatus: Equatable, Sendable {
    /// `Proposed (awaiting Alex approval)` — needs an explicit decision.
    case proposed
    /// `Pending (auto-apply after <date>)` is opt-out: a dreaming run applies
    /// it once the date has passed, unless it is rejected. A bare `Pending`
    /// (governance and safety changes) is applied only after an explicit
    /// `Approved` (feedback-processing.md). `autoApplyDate` is the ISO date of
    /// an `auto-apply after <date>` clause, and `nil` without one.
    case pending(autoApplyDate: String?)
    /// `Approved …` — will be applied by the next dreaming run.
    case approved
    /// `Rejected …` — declined; will not be applied.
    case rejected
    /// `Applied — <date>` or `Partially applied` — already (partly) applied
    /// to SKILL.md.
    case applied(date: String?)
    /// `Superseded`, `Withdrawn` or `Obsolete …` — settled without a decision:
    /// a dreaming run folded it into another proposal or dropped it. Kept
    /// verbatim; nothing to approve.
    case closed(String)
    /// Any status string we don't recognize; preserved verbatim for display,
    /// and still shown with the undecided ones (see `isAwaitingDecision`).
    case unknown(String)

    /// Classify a raw status value (the text after `**Status:**`) by its
    /// leading word, case-insensitively, after any leading emoji or
    /// punctuation (`✅ Approved (…)`). The leading word is what a dreaming
    /// run keys on, so anchoring on it keeps the app and engine in agreement.
    static func parse(_ rawValue: String) -> ProposalStatus {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let words = String(trimmed.drop { !$0.isLetter })
        let lower = words.lowercased()
        if lower.hasPrefix("approved") { return .approved }
        if lower.hasPrefix("rejected") || lower.hasPrefix("declined") { return .rejected }
        if lower.hasPrefix("applied") || lower.hasPrefix("partially applied") {
            return .applied(date: Self.firstISODate(in: trimmed))
        }
        if Self.closedWords.contains(where: lower.hasPrefix) { return .closed(words) }
        if lower.hasPrefix("pending") { return .pending(autoApplyDate: Self.autoApplyDate(in: trimmed)) }
        if lower.hasPrefix("proposed") { return .proposed }
        return .unknown(trimmed)
    }

    /// Leading words of a proposal a dreaming run settled without a decision.
    private static let closedWords = ["superseded", "withdrawn", "obsolete"]

    /// True when the proposal is still waiting on the user's call — i.e. it
    /// drives the sidebar badge count and is listed above Resolved.
    ///
    /// An unknown status counts as waiting (audit #64), as an unknown Wishlist
    /// status counts as open: filing it under Resolved would hide a proposal
    /// nobody has decided on. Its card offers no primary Approve, though
    /// (`hasPrimaryApprove`): approving tells the next dreaming run to apply
    /// a change Scout can't tell is still wanted.
    var isAwaitingDecision: Bool {
        switch self {
        case .proposed, .pending, .unknown: return true
        case .approved, .rejected, .applied, .closed: return false
        }
    }

    /// Whether the card leads with Approve as its primary action. Off for an
    /// unknown status, where Approve and Decline are both secondary.
    var hasPrimaryApprove: Bool {
        switch self {
        case .proposed, .pending: return true
        case .unknown, .approved, .rejected, .applied, .closed: return false
        }
    }

    var isResolved: Bool { !isAwaitingDecision }

    /// Short label for the status pill. A rejected proposal reads "Declined",
    /// the word on the button that rejects it (audit #64); the file keeps the
    /// engine's `Rejected`.
    var displayName: String {
        switch self {
        case .proposed:           return "Proposed"
        case .pending:            return "Pending"
        case .approved:           return "Approved"
        case .rejected:           return "Declined"
        case .applied:            return "Applied"
        case .closed(let words):  return words.prefix { $0.isLetter }.capitalized
        case .unknown(let raw):   return raw
        }
    }

    /// The `Pending` auto-apply day as a calendar date, or `nil` without one.
    func autoApplyDay(calendar: Calendar = .current) -> Date? {
        guard case .pending(let iso?) = self else { return nil }
        let parts = iso.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }

    /// The line a card shows under its title for a status that needs
    /// explaining (audit #62, #64): when a pending proposal applies by itself
    /// (or that it never does without approval), or that an unknown status
    /// still waits on a decision. `nil` otherwise.
    func explanation(today: Date = .now, calendar: Calendar = .current) -> String? {
        switch self {
        case .pending:
            guard let day = autoApplyDay(calendar: calendar) else {
                // A bare `Pending` is the governance and safety form: no run
                // applies it until it says `Approved`.
                return "Waits for your approval: Scout applies it only after you approve it."
            }
            let formatted = day.formatted(.dateTime.month(.abbreviated).day().year())
            if calendar.startOfDay(for: today) > day {
                return "Its wait ended \(formatted): the next dreaming run applies it unless you decline it."
            }
            return "Applies automatically after \(formatted) unless you decline it."
        case .unknown(let raw):
            return "Scout doesn't recognise the status “\(raw)”, so it is kept here. Read the proposal before you decide: approving tells the next dreaming run to apply it."
        case .proposed, .approved, .rejected, .applied, .closed:
            return nil
        }
    }

    // MARK: - Helpers

    /// The clause must open the status's note: right after `Pending`, at the
    /// start, or after `(`, `;` or `,`. So "do not auto-apply after <date>"
    /// or "(not auto-apply after <date>)" is never read as auto-applying.
    private static let autoApplyRe = try! NSRegularExpression(
        pattern: #"(?:^|[(;,]|\bpending\b)\s*auto[- ]?apply after:?\s*(\d{4}-\d{2}-\d{2})"#,
        options: [.caseInsensitive])

    /// The date of an `auto-apply after <date>` clause, and only that: any
    /// other date in a `Pending` status says nothing about applying it.
    private static func autoApplyDate(in s: String) -> String? {
        let ns = s as NSString
        guard let m = autoApplyRe.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range(at: 1))
    }

    /// First `yyyy-MM-dd` substring: an `Applied` date.
    private static func firstISODate(in s: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: #"\d{4}-\d{2}-\d{2}"#) else { return nil }
        let ns = s as NSString
        guard let m = re.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range)
    }
}
