import Foundation

/// View mode for the Action Items tab — the editorial reading List (default),
/// a status Board (issue #15), or the topic board (Raven-Scout/Scout#340). Persists across launches via
/// `@SceneStorage("actionItemsView")`.
enum ActionItemsViewMode: String, CaseIterable, Identifiable, Hashable {
    case list
    case board
    case topics

    var id: String { rawValue }

    static let `default`: ActionItemsViewMode = .list

    var displayName: String {
        switch self {
        case .list:  return "List"
        case .board: return "Board"
        case .topics: return "Topics"
        }
    }
}
