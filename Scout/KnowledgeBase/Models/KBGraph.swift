import SwiftUI

/// Entity category of a knowledge-base note. Drives the node color in the
/// local graph and the legend. Kept deliberately small so the graph reads as a
/// map, not a stoplight.
nonisolated enum KBEntityGroup: String, Equatable, Hashable, CaseIterable {
    case people, projects, issues, channels, ontology, research, other

    /// Classify a note. The ontology `type:` the plugin writes in frontmatter
    /// wins; the path fallback (whole components, not substrings — so
    /// `tissue-sampling.md` is not an issue) covers hub notes without one.
    static func of(_ relPath: String, type: String? = nil) -> KBEntityGroup {
        switch type {
        case "person": return .people
        case "project": return .projects
        case "task": return .issues
        default: break
        }
        let components = relPath.lowercased().split(separator: "/").map(String.init)
        let stem = components.last.map { ($0 as NSString).deletingPathExtension } ?? ""
        let dirs = components.dropLast()
        if dirs.contains("people") || stem == "people" { return .people }
        if dirs.contains("projects") || stem == "projects" { return .projects }
        if dirs.contains("issues") || stem == "issues" { return .issues }
        if dirs.contains("channels") || stem == "channels" { return .channels }
        if dirs.contains("ontology") { return .ontology }
        if dirs.contains("research-queue") || dirs.contains("review-queue")
            || stem == "research-queue" || stem == "review-queue" { return .research }
        return .other
    }

    var label: String {
        switch self {
        case .people: return "People"
        case .projects: return "Projects"
        case .issues: return "Issues"
        case .channels: return "Channels"
        case .ontology: return "Ontology"
        case .research: return "Research"
        case .other: return "Other"
        }
    }

    var color: Color {
        switch self {
        case .people:   return DS.Priority.personal
        case .projects: return DS.SlotType.consolidation
        case .issues:   return DS.Priority.urgent
        case .channels: return DS.Accent.fill
        case .ontology: return DS.SlotType.dreaming
        case .research: return DS.SlotType.research
        case .other:    return DS.Ink.p3
        }
    }
}

/// One outgoing `[[wikilink]]` from a note, with its resolved target path (nil
/// when the link points at a note that doesn't exist in the KB).
nonisolated struct KBLink: Identifiable, Equatable {
    let target: String        // original link text (before any `|alias`)
    let resolved: String?     // repo-relative path, or nil if dangling
    var id: String { target }
}

/// A note that links *to* the current one.
nonisolated struct KBBacklink: Identifiable, Equatable {
    let path: String
    let name: String
    let excerpt: String
    var id: String { path }
}

/// A content-search hit.
nonisolated struct KBSearchHit: Identifiable, Equatable {
    let path: String
    let name: String
    let snippet: String
    var id: String { path }
}

// MARK: - Graph

nonisolated struct KBGraphNode: Identifiable, Equatable {
    let id: String           // repo-relative path
    let label: String
    let group: KBEntityGroup
    let degree: Int
    let isCenter: Bool
}

nonisolated struct KBGraphEdge: Equatable, Hashable {
    let from: String
    let to: String
}

nonisolated struct KBGraph: Equatable {
    let nodes: [KBGraphNode]
    let edges: [KBGraphEdge]
    static let empty = KBGraph(nodes: [], edges: [])
}

/// Precomputed wikilink index: each note's display stem → its path, each note's
/// outgoing link targets (original case), the note text itself (read once per
/// reparse; serves backlink excerpts and full-text search without disk I/O),
/// and the note's frontmatter `type:` (drives graph grouping). Rebuilt on
/// every reparse.
nonisolated struct KBIndex: Equatable {
    let stemToPath: [String: String]
    let outByFile: [String: [String]]
    let textByFile: [String: String]
    let typeByFile: [String: String]
    static let empty = KBIndex(stemToPath: [:], outByFile: [:], textByFile: [:], typeByFile: [:])
}
