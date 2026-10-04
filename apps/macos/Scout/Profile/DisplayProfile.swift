import Foundation

/// The user's display choices, kept in `scout-profile.json` in the vault root.
/// Only the Action Items section exists so far. Other sections are added with
/// the features that read them; until then the codec reports them as unknown
/// and preserves them on write.
nonisolated struct DisplayProfile: Equatable, Sendable {
    static let currentSchema = 1

    var actionItems = ActionItemsDisplay()

    /// One leaf of the file: its key path below the root and its value.
    struct Entry: Equatable, Sendable {
        enum Value: Equatable, Sendable {
            case string(String)
            case bool(Bool)
        }
        let path: [String]
        let value: Value
    }

    /// Every leaf this profile describes, in a fixed order.
    var entries: [Entry] {
        actionItems.entries.map { Entry(path: ["actionItems"] + $0.path, value: $0.value) }
    }

    /// The leaves whose value differs from `old`. A write patches only these,
    /// so keys the user never touched keep following the app's defaults.
    func changedEntries(from old: DisplayProfile) -> [Entry] {
        let before = Dictionary(uniqueKeysWithValues: old.entries.map { ($0.path, $0.value) })
        return entries.filter { before[$0.path] != $0.value }
    }
}

/// Action Items display options (#52). Every default is today's rendering.
nonisolated struct ActionItemsDisplay: Equatable, Sendable {
    enum Sort: String, CaseIterable, Identifiable, Sendable {
        case fileOrder, alphabetical
        var id: String { rawValue }
        var displayName: String {
            switch self {
            case .fileOrder:    return "File order"
            case .alphabetical: return "Alphabetical"
            }
        }
    }

    enum Grouping: String, CaseIterable, Identifiable, Sendable {
        case section, none
        var id: String { rawValue }
        var displayName: String {
            switch self {
            case .section: return "By section"
            case .none:    return "One list"
            }
        }
    }

    enum Density: String, CaseIterable, Identifiable, Sendable {
        case comfortable, compact
        var id: String { rawValue }
        var displayName: String {
            switch self {
            case .comfortable: return "Comfortable"
            case .compact:     return "Compact"
            }
        }
    }

    /// What the collapsed list card and the board card show. The expanded
    /// card always shows everything.
    struct Fields: Equatable, Sendable {
        var refs = true
        var snooze = true
        var comments = false
    }

    var defaultView: ActionItemsViewMode = .list
    var sort: Sort = .fileOrder
    var grouping: Grouping = .section
    var density: Density = .comfortable
    var fields = Fields()

    var entries: [DisplayProfile.Entry] {
        [
            .init(path: ["defaultView"], value: .string(defaultView.rawValue)),
            .init(path: ["sort"], value: .string(sort.rawValue)),
            .init(path: ["group"], value: .string(grouping.rawValue)),
            .init(path: ["density"], value: .string(density.rawValue)),
            .init(path: ["fields", "refs"], value: .bool(fields.refs)),
            .init(path: ["fields", "snooze"], value: .bool(fields.snooze)),
            .init(path: ["fields", "comments"], value: .bool(fields.comments)),
        ]
    }
}
