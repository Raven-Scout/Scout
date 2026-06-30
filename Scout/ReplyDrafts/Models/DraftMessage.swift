import Foundation

/// One message in a draft's thread-context block — the prior messages on the
/// topic Scout used to ground the reply. Rendered in the collapsible "Thread"
/// section under the draft so the user can see what the conversation is about.
///
/// Parsed from a context line of the form `- [YYYY-MM-DD] Sender: text`.
nonisolated struct DraftMessage: Identifiable, Equatable, Sendable {
    /// `YYYY-MM-DD` (or whatever date string Scout wrote); may be empty.
    let date: String
    /// Who sent it (e.g. "Lucia Hallonová" or "Vojta (you)").
    let sender: String
    /// One-line paraphrase or quote of the message.
    let text: String

    let id: String
}
