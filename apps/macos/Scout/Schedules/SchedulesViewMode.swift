import Foundation

/// View mode for the Schedules tab — Table (default), Cards, or Timeline.
/// Persists across app launches via `@SceneStorage("schedulesView")`.
enum SchedulesViewMode: String, CaseIterable, Identifiable, Hashable {
    case table
    case cards
    case timeline

    var id: String { rawValue }

    /// The default view when no scene-storage value exists.
    static let `default`: SchedulesViewMode = .table

    /// All three views are available — Timeline ships in the Scout.html
    /// design parity revamp with full gap-collapse, NOW marker, and
    /// alternating-side card layout.
    var isAvailable: Bool { true }

    /// Display label for the segmented picker.
    var displayName: String {
        switch self {
        case .table:    return "Table"
        case .cards:    return "Cards"
        case .timeline: return "Timeline"
        }
    }
}
