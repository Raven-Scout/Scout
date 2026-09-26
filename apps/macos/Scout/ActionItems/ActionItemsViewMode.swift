import Foundation

/// View mode for the Action Items tab — the editorial reading List (default)
/// or a status Board (issue #15). Persists across launches via
/// `@SceneStorage("actionItemsView")`.
enum ActionItemsViewMode: String, CaseIterable, Identifiable, Hashable {
    case list
    case board

    var id: String { rawValue }

    static let `default`: ActionItemsViewMode = .list

    var displayName: String {
        switch self {
        case .list:  return "List"
        case .board: return "Board"
        }
    }
}
