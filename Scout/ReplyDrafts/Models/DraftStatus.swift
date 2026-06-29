import Foundation

/// Lifecycle status of a prepared reply draft, parsed from the `status:` field
/// of a `drafts/<TAG>.md` file.
///
/// The vocabulary is the strict, lowercase contract the scout-plugin writes and
/// re-reads: a draft moves `draft` → `sent` (the user sent it himself) or
/// `dismissed` (no longer needed). The app only ever flips this field — it
/// never sends anything. The canonical word is what the plugin keys on, so the
/// app must write exactly `draft` / `sent` / `dismissed` back.
nonisolated enum DraftStatus: Equatable, Sendable {
    /// `draft` — prepared, awaiting the user to send it.
    case draft
    /// `sent` — the user has sent the reply himself.
    case sent
    /// `dismissed` — the reply is no longer needed.
    case dismissed
    /// Any status string we don't recognize; preserved verbatim for display.
    case unknown(String)

    /// Classify a raw `status:` value, case-insensitively.
    static func parse(_ rawValue: String) -> DraftStatus {
        switch rawValue.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "draft":     return .draft
        case "sent":      return .sent
        case "dismissed": return .dismissed
        case let other:   return other.isEmpty ? .draft : .unknown(other)
        }
    }

    /// The canonical lowercase word written back into the file. Must match the
    /// plugin's `status:` contract exactly so a re-read by Scout round-trips.
    var fileValue: String {
        switch self {
        case .draft:            return "draft"
        case .sent:             return "sent"
        case .dismissed:        return "dismissed"
        case .unknown(let raw): return raw
        }
    }

    /// True while the draft still needs the user's action — drives the sidebar
    /// badge and the send/dismiss buttons.
    var isAwaitingAction: Bool {
        if case .draft = self { return true }
        return false
    }

    var isResolved: Bool { !isAwaitingAction }

    /// Short label for the status pill.
    var displayName: String {
        switch self {
        case .draft:            return "Draft"
        case .sent:             return "Sent"
        case .dismissed:        return "Dismissed"
        case .unknown(let raw): return raw
        }
    }
}
