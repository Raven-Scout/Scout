import Foundation

/// One quote-line comment bound to a task.
/// Source shape: ``  > author (2026-04-18 10:20 AM ET): text``
nonisolated struct TaskComment: Equatable, Hashable, Sendable {
    let author: String
    /// Free-form timestamp as written in the file. May be empty.
    let timestamp: String
    let text: String
}
