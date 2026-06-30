import Foundation

/// One turn in the per-draft AI assistant chat.
nonisolated struct ChatMessage: Identifiable, Equatable, Sendable {
    enum Role: Equatable, Sendable { case user, assistant, error }
    let role: Role
    let text: String
    let id: String
}
