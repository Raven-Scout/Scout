import Foundation

/// Lifecycle status of a dreaming proposal, parsed from the `**Status:**` line
/// of a proposal section in `dreaming-proposals.md`.
///
/// The status vocabulary mirrors the scout-plugin feedback-processing spec:
/// a proposal moves `Proposed` / `Pending` → `Approved` or `Rejected`, and a
/// later dreaming run flips an approved (or ripe opt-out `Pending`) proposal to
/// `Applied`. The app only ever writes `Approved` / `Rejected` — applying the
/// underlying SKILL.md change stays with the dreaming run.
nonisolated enum ProposalStatus: Equatable, Sendable {
    /// `Proposed (awaiting Alex approval)` — needs an explicit decision.
    case proposed
    /// `Pending (auto-apply after <date>)` — opt-out; auto-applies unless
    /// rejected. `autoApplyDate` is the ISO date when present.
    case pending(autoApplyDate: String?)
    /// `Approved …` — will be applied by the next dreaming run.
    case approved
    /// `Rejected …` — declined; will not be applied.
    case rejected
    /// `Applied — <date>` — already applied to SKILL.md.
    case applied(date: String?)
    /// Any status string we don't recognize; preserved verbatim for display,
    /// and still awaiting a decision (see `isAwaitingDecision`).
    case unknown(String)

    /// Classify a raw status value (the text after `**Status:**`) by its
    /// leading word, case-insensitively. The leading word is what a dreaming
    /// run keys on, so anchoring on it keeps the app and engine in agreement.
    static func parse(_ rawValue: String) -> ProposalStatus {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let lower = trimmed.lowercased()
        if lower.hasPrefix("approved") { return .approved }
        if lower.hasPrefix("rejected") || lower.hasPrefix("declined") { return .rejected }
        if lower.hasPrefix("applied") { return .applied(date: Self.firstISODate(in: trimmed)) }
        if lower.hasPrefix("pending") { return .pending(autoApplyDate: Self.firstISODate(in: trimmed)) }
        if lower.hasPrefix("proposed") { return .proposed }
        return .unknown(trimmed)
    }

    /// True when the proposal is still waiting on the user's call — i.e. it
    /// drives the sidebar badge count and gets Approve/Decline buttons.
    ///
    /// An unknown status counts as waiting (audit #64), as an unknown Wishlist
    /// status counts as open: filing it under Resolved would hide a proposal
    /// nobody has decided on. Approve or Decline settles it.
    var isAwaitingDecision: Bool {
        switch self {
        case .proposed, .pending, .unknown: return true
        case .approved, .rejected, .applied: return false
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
    /// explaining (audit #62, #64): when a pending proposal applies by itself,
    /// or that an unknown status still waits on a decision. `nil` otherwise.
    func explanation(today: Date = .now, calendar: Calendar = .current) -> String? {
        switch self {
        case .pending:
            guard let day = autoApplyDay(calendar: calendar) else {
                return "Applies automatically at a later dreaming run unless you decline it."
            }
            let formatted = day.formatted(.dateTime.month(.abbreviated).day().year())
            if calendar.startOfDay(for: today) > day {
                return "Its wait ended \(formatted): the next dreaming run applies it unless you decline it."
            }
            return "Applies automatically after \(formatted) unless you decline it."
        case .unknown(let raw):
            return "Scout doesn't recognise the status “\(raw)”, so this still waits on you. Approve or decline it to settle it."
        case .proposed, .approved, .rejected, .applied:
            return nil
        }
    }

    // MARK: - Helpers

    /// First `yyyy-MM-dd` substring, used to surface a `Pending` auto-apply
    /// date or an `Applied` date in the pill subtitle.
    private static func firstISODate(in s: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: #"\d{4}-\d{2}-\d{2}"#) else { return nil }
        let ns = s as NSString
        guard let m = re.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range)
    }
}
