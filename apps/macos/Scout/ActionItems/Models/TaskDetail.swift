import Foundation

/// One indented line of context under an action item: a sub-bullet, or a run
/// of continuation lines that belong to the sub-bullet above it.
///
/// The engine's own parser keeps these as `ActionItem.details`
/// (`engine/scout/action_items/parser.py`). Before this type the app sent
/// them to the section's prose, where no card showed them, so an item written
/// as a bold title plus sub-bullets rendered as a bare title.
nonisolated struct TaskDetail: Equatable, Hashable, Sendable {
    /// Nesting below the owning task: 0 = direct sub-bullet, 1 = one level
    /// deeper, and so on.
    let depth: Int
    /// Raw markdown without the bullet marker. Continuation lines are joined
    /// with "\n", with the bullet's own indent removed so code inside a fence
    /// keeps its relative indentation.
    let text: String
}
