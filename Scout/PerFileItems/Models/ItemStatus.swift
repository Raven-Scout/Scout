// Scout/PerFileItems/Models/ItemStatus.swift
import Foundation

/// Lifecycle of a per-file Wishlist/Research item (frontmatter `status:`).
nonisolated enum ItemStatus: Equatable, Sendable {
    case open, inProgress, done, dropped, unknown(String)

    static func parse(_ raw: String) -> ItemStatus {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        switch trimmed.lowercased() {
        case "open", "": return .open
        case "in-progress", "in progress", "inprogress": return .inProgress
        case "done": return .done
        case "dropped": return .dropped
        default: return .unknown(trimmed)
        }
    }

    /// open/in-progress are active (Awaiting); done/dropped/unknown are resolved.
    var isActive: Bool {
        switch self {
        case .open, .inProgress: return true
        case .done, .dropped, .unknown: return false
        }
    }

    var displayName: String {
        switch self {
        case .open: return "Open"
        case .inProgress: return "In Progress"
        case .done: return "Done"
        case .dropped: return "Dropped"
        case .unknown(let raw): return raw
        }
    }

    /// The exact value written back into frontmatter.
    var frontmatterValue: String {
        switch self {
        case .open: return "open"
        case .inProgress: return "in-progress"
        case .done: return "done"
        case .dropped: return "dropped"
        case .unknown(let raw): return raw
        }
    }
}
