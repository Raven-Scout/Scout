import Foundation

/// View mode for the Action Items tab: the editorial reading List (default),
/// a status Board (issue #15), or the topic board (Raven-Scout/Scout#340). The
/// tab opens in `scout-profile.json`'s `actionItems.defaultView`; the toolbar
/// switch changes the session's view through `DisplayProfileService.currentView`.
nonisolated enum ActionItemsViewMode: String, CaseIterable, Identifiable, Hashable, Sendable {
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
