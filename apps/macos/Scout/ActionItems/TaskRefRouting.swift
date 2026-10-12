import SwiftUI

// In-app targets for a task's `- Refs:` chips (Raven-Scout/Scout#295): an
// entity chip opens its note in the Knowledge Base tab, a cross-ref chip
// scrolls the list to the tagged item.

/// Opens a KB note by wikilink target (e.g. `people/alex`) in the Knowledge
/// Base tab. Set on the Action Items tab; nil elsewhere, where entity links
/// keep opening `obsidian://`.
private struct OpenKBNoteKey: EnvironmentKey {
    static let defaultValue: ((String) -> Void)? = nil
}

/// Scrolls the Action Items list to the item tagged `[#TAG]` (tag without
/// `#`). Set by the List view; nil elsewhere, where cross-ref chips stay inert.
private struct RevealTaggedTaskKey: EnvironmentKey {
    static let defaultValue: ((String) -> Void)? = nil
}

/// The task a cross-ref just scrolled to, outlined briefly so it is findable.
private struct HighlightedTaskIDKey: EnvironmentKey {
    static let defaultValue: UUID? = nil
}

extension EnvironmentValues {
    var openKBNote: ((String) -> Void)? {
        get { self[OpenKBNoteKey.self] }
        set { self[OpenKBNoteKey.self] = newValue }
    }

    var revealTaggedTask: ((String) -> Void)? {
        get { self[RevealTaggedTaskKey.self] }
        set { self[RevealTaggedTaskKey.self] = newValue }
    }

    var highlightedTaskID: UUID? {
        get { self[HighlightedTaskIDKey.self] }
        set { self[HighlightedTaskIDKey.self] = newValue }
    }
}

/// What the Knowledge Base tab does with a note another tab asked it to open.
enum KBNoteRequest {
    enum Outcome: Equatable {
        /// The index isn't built yet; try again when the service has loaded.
        case wait
        /// Select this repo-relative path.
        case open(String)
        /// No such note, or no KB: open it in Obsidian instead.
        case fallback
    }

    static func outcome(target: String, state: KnowledgeBaseService.State,
                        resolve: (String) -> String?) -> Outcome {
        switch state {
        case .idle, .loading: return .wait
        case .loaded:         return resolve(target).map(Outcome.open) ?? .fallback
        case .missing, .failed: return .fallback
        }
    }

    /// The `obsidian://` URL entity chips opened before Raven-Scout/Scout#295.
    static func fallbackURL(for target: String) -> URL? {
        TaskDeepLink.entity(path: target, label: nil).openURL(linearWorkspace: "")
    }
}

/// Finds the row a cross-ref chip points at in the sections the List shows.
enum CrossRefLocator {
    struct Location: Equatable {
        let sectionID: UUID
        /// Position in the section's (filtered) task list — what `TaskWindow`
        /// counts, so `reveal(index)` builds the row.
        let index: Int
        let taskID: UUID
    }

    /// The first open row tagged `tag` (case-insensitive). The Done drawer is
    /// skipped: it is collapsed by default, so there is no row to scroll to.
    static func locate(tag: String, in sections: [ActionSection]) -> Location? {
        let wanted = tag.lowercased()
        for section in sections where section.kind != .done {
            if let index = section.tasks.firstIndex(where: { $0.shortPrefix?.lowercased() == wanted }) {
                return Location(sectionID: section.id, index: index, taskID: section.tasks[index].id)
            }
        }
        return nil
    }
}
