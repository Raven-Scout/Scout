import SwiftUI

/// Entity category of a knowledge-base note. Drives the node color in the
/// local graph and the legend. Kept deliberately small so the graph reads as a
/// map, not a stoplight.
nonisolated enum KBEntityGroup: String, Equatable, Hashable, CaseIterable {
    case people, projects, issues, channels, ontology, research
    /// The vault's layers (sources → topics → projects) and the folders Scout
    /// keeps for itself. Before these existed, most of the vault — sources,
    /// topics, personal notes, session logs, the mistake audit — rendered as
    /// one grey "Other" blob on the map.
    case topics, sources, personal, logs, audits
    case other

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
        if dirs.contains("topics") || stem == "topics" { return .topics }
        if dirs.contains("sources") || stem == "sources" { return .sources }
        if dirs.contains("personal") { return .personal }
        if dirs.contains("session-log") || dirs.contains("enrichment-qa-log")
            || stem == "session-log" { return .logs }
        if dirs.contains("scout-mistake-audit") || dirs.contains("kg-audits")
            || stem == "scout-mistake-audit" { return .audits }
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
        case .topics: return "Topics"
        case .sources: return "Sources"
        case .personal: return "Personal"
        case .logs: return "Logs"
        case .audits: return "Audits"
        case .other: return "Other"
        }
    }

    var color: Color {
        switch self {
        case .people:   return DS.Priority.personal
        case .projects: return DS.SlotType.consolidation
        case .issues:   return DS.Priority.urgent
        case .channels: return DS.Accent.fill
        case .research: return DS.SlotType.research
        case .other:    return DS.Ink.p3
        // Ontology was the dreaming violet, which sits within a few points of
        // People's purple — the two read as one colour on the map.
        case .ontology, .topics, .sources, .personal, .logs, .audits: return swatch.color
        }
    }

    /// The group's colour as raw light/dark values, for surfaces that can't
    /// take a SwiftUI `Color` (the web map). Matches `color` for every group.
    var swatch: DS.Swatch {
        switch self {
        case .people:   return DS.Swatch(name: "KBPeople", light: DS.RGB(0.540, 0.440, 0.740), dark: DS.RGB(0.700, 0.620, 0.880))
        case .projects: return DS.Palette.slotConsolidation
        case .issues:   return DS.Swatch(name: "KBIssues", light: DS.RGB(0.750, 0.310, 0.200), dark: DS.RGB(0.880, 0.450, 0.340))
        case .channels: return DS.Palette.accentFill
        case .ontology: return DS.Swatch(name: "KBOntology", light: DS.RGB(0.300, 0.380, 0.720), dark: DS.RGB(0.560, 0.640, 0.950))
        case .research: return DS.Palette.slotResearch
        case .topics:   return DS.Swatch(name: "KBTopics", light: DS.RGB(0.180, 0.560, 0.560), dark: DS.RGB(0.380, 0.760, 0.740))
        case .sources:  return DS.Swatch(name: "KBSources", light: DS.RGB(0.660, 0.540, 0.330), dark: DS.RGB(0.820, 0.710, 0.520))
        case .personal: return DS.Swatch(name: "KBPersonal", light: DS.RGB(0.780, 0.380, 0.540), dark: DS.RGB(0.920, 0.560, 0.680))
        case .logs:     return DS.Swatch(name: "KBLogs", light: DS.RGB(0.470, 0.530, 0.620), dark: DS.RGB(0.600, 0.650, 0.730))
        case .audits:   return DS.Swatch(name: "KBAudits", light: DS.RGB(0.520, 0.540, 0.200), dark: DS.RGB(0.720, 0.730, 0.420))
        case .other:    return DS.Palette.inkP3
        }
    }
}

/// Whether a note counts toward vault health. On a mature vault the health
/// rows were dominated by notes nobody maintains as live knowledge: dangling
/// links almost all came from one archived snapshot, and the orphans were
/// receipts and invoices. Those are set aside and counted on their own.
nonisolated enum KBNoteKind: String, Equatable, CaseIterable {
    case live
    /// Anything under a folder named exactly `archive` or `archived`, at any depth.
    case archived
    /// A receipt or invoice: under a folder named exactly receipt(s) or
    /// invoice(s), or with one of those as a word of its file name or of its
    /// frontmatter `type:`.
    case receipt

    private static let receiptWords: Set<String> = ["receipt", "receipts", "invoice", "invoices"]

    /// The same rule `/scout-status` applies (plugin `commands/scout-status.md`,
    /// KB Health): folders relative to `knowledge-base/` match whole, names and
    /// types by word, case ignored.
    static func of(_ relPath: String, type: String? = nil) -> KBNoteKind {
        var components = relPath.lowercased().split(separator: "/").map(String.init)
        if components.first == "knowledge-base" { components.removeFirst() }
        let folders = components.dropLast()
        if folders.contains(where: { $0 == "archive" || $0 == "archived" }) { return .archived }
        if folders.contains(where: receiptWords.contains) { return .receipt }
        let stem = components.last.map { ($0 as NSString).deletingPathExtension } ?? ""
        if words(stem).contains(where: receiptWords.contains) { return .receipt }
        if let type, words(type.lowercased()).contains(where: receiptWords.contains) { return .receipt }
        return .live
    }

    private static func words(_ s: String) -> [String] {
        s.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init)
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

extension KBGraph {
    /// The top `maxNodes` nodes by degree (ties broken by id ascending for a
    /// stable layout), plus only the edges whose endpoints are both kept.
    /// Returns `self` unchanged when already within the cap. Seeds the overview
    /// with the vault's most-connected "spine" instead of all N notes.
    func topHubs(maxNodes: Int) -> KBGraph {
        guard nodes.count > maxNodes else { return self }
        let kept = nodes
            .sorted { a, b in a.degree != b.degree ? a.degree > b.degree : a.id < b.id }
            .prefix(maxNodes)
        let keptIds = Set(kept.map(\.id))
        return KBGraph(nodes: Array(kept),
                       edges: edges.filter { keptIds.contains($0.from) && keptIds.contains($0.to) })
    }

    /// Keep nodes in `types`, with `degree >= minDegree`, and (when
    /// `hideOrphans`) `degree > 0`. A center node is ALWAYS kept so a re-rooted
    /// view is never emptied by a filter. Edges with a removed endpoint drop.
    /// Degree here is the node's degree over the whole KB, not within the
    /// currently rendered subgraph — so "hide orphans" only drops notes with no
    /// links anywhere, not hub-view nodes whose neighbours happen to be capped out.
    func filtered(types: Set<KBEntityGroup>, hideOrphans: Bool, minDegree: Int) -> KBGraph {
        let keptNodes = nodes.filter { n in
            if n.isCenter { return true }
            guard types.contains(n.group) else { return false }
            if n.degree < minDegree { return false }
            if hideOrphans && n.degree == 0 { return false }
            return true
        }
        let keptIds = Set(keptNodes.map(\.id))
        return KBGraph(nodes: keptNodes,
                       edges: edges.filter { keptIds.contains($0.from) && keptIds.contains($0.to) })
    }
}

/// Precomputed wikilink index: each note's display stem → its path, each note's
/// outgoing link targets (original case) and what each one resolves to, the
/// resulting undirected edge set, the note text itself (read once per
/// reparse; serves backlink excerpts and full-text search without disk I/O),
/// and the note's frontmatter `type:` (drives graph grouping). Built off the
/// main actor on every reparse, so readers only do lookups.
nonisolated struct KBIndex: Equatable {
    /// Bare-stem resolution: the last note with that stem in tree order.
    let stemToPath: [String: String]
    /// Every note path per lowercased stem, in tree order — path-form links
    /// pick among these, so a stem collision can't hide `[[people/alex]]`.
    let stemPaths: [String: [String]]
    let outByFile: [String: [String]]
    /// `outByFile`'s targets resolved, index-aligned (nil = unresolved).
    let resolvedOut: [String: [String?]]
    /// Unique undirected edges between resolved notes (no self-links).
    let edges: Set<KBGraphEdge>
    let textByFile: [String: String]
    let typeByFile: [String: String]
    /// Lowercased names of every non-hidden vault file outside the KB's own
    /// notes (`.md` by stem, others by full name). Lets the dangling check
    /// tell a link to a vault file from a genuinely missing note.
    let vaultNames: Set<String>

    init(stemPaths: [String: [String]], outByFile: [String: [String]],
         textByFile: [String: String], typeByFile: [String: String], vaultNames: Set<String>) {
        let stemToPath = stemPaths.compactMapValues(\.last)
        var resolvedOut: [String: [String?]] = [:]
        var edges = Set<KBGraphEdge>()
        for (from, targets) in outByFile {
            let resolved = targets.map {
                Self.resolve($0, from: from, stemToPath: stemToPath, stemPaths: stemPaths)
            }
            resolvedOut[from] = resolved
            for case let to? in resolved where to != from {
                edges.insert(from < to ? KBGraphEdge(from: from, to: to) : KBGraphEdge(from: to, to: from))
            }
        }
        self.stemToPath = stemToPath
        self.stemPaths = stemPaths
        self.outByFile = outByFile
        self.resolvedOut = resolvedOut
        self.edges = edges
        self.textByFile = textByFile
        self.typeByFile = typeByFile
        self.vaultNames = vaultNames
    }

    static let empty = KBIndex(stemPaths: [:], outByFile: [:], textByFile: [:], typeByFile: [:],
                               vaultNames: [])
}

extension KBIndex {
    /// The lookup key for a `[[target]]`: any `#heading` / `#^block` anchor
    /// and a trailing `.md` removed, trimmed, lowercased.
    nonisolated static func linkKey(_ target: String) -> String {
        var key = target
        if let hash = key.firstIndex(of: "#") { key = String(key[..<hash]) }
        key = key.trimmingCharacters(in: .whitespaces).lowercased()
        if key.hasSuffix(".md") { key.removeLast(3) }
        return key
    }

    /// Resolve a `[[target]]` written in `source` to a note path, following
    /// Obsidian for the forms the vault uses: a bare stem (`[[alex]]`), a
    /// path matched as a suffix of the note's path (`[[people/alex]]`), a
    /// heading/block anchor (`[[alex#Role]]`; `[[#Role]]` is `source`
    /// itself) and an explicit `.md`. Nil when nothing matches.
    nonisolated func resolve(_ target: String, from source: String? = nil) -> String? {
        Self.resolve(target, from: source, stemToPath: stemToPath, stemPaths: stemPaths)
    }

    nonisolated static func resolve(_ target: String, from source: String?,
                                    stemToPath: [String: String],
                                    stemPaths: [String: [String]]) -> String? {
        let key = linkKey(target)
        if key.isEmpty {
            return target.trimmingCharacters(in: .whitespaces).hasPrefix("#") ? source : nil
        }
        guard let slash = key.lastIndex(of: "/") else { return stemToPath[key] }
        let candidates = stemPaths[String(key[key.index(after: slash)...])] ?? []
        return candidates.first { path in
            let bare = (path.lowercased() as NSString).deletingPathExtension
            return bare == key || bare.hasSuffix("/" + key)
        }
    }

    /// A `[[PROJ-1234]]`-style issue id. The vault links tickets by id on
    /// purpose; with no note behind them they are references, not breakage.
    nonisolated static func isTicketID(_ target: String) -> Bool {
        target.range(of: #"^[A-Z][A-Z0-9]{1,9}-\d+$"#, options: .regularExpression) != nil
    }

    /// True when the link names a file elsewhere in the vault (e.g.
    /// `[[action-items-2026-04-27]]`). Obsidian resolves those, but the
    /// KB-only index can't, so they mustn't count as dangling.
    nonisolated func existsInVault(_ target: String) -> Bool {
        let key = Self.linkKey(target)
        guard let name = key.split(separator: "/").last else { return false }
        return vaultNames.contains(String(name))
    }
}

// MARK: - Network stats

/// An outgoing `[[target]]` that resolves to no note.
nonisolated struct KBDanglingLink: Identifiable, Equatable {
    let source: String     // note holding the broken link (relative path)
    let target: String     // unresolved [[target]] text, original case
    var id: String { source + "→" + target }
}

nonisolated struct KBHub: Identifiable, Equatable {
    let path: String
    let degree: Int
    var id: String { path }
}

nonisolated struct KBTypeCount: Identifiable, Equatable {
    let group: KBEntityGroup
    let count: Int
    var id: KBEntityGroup { group }
}

/// Whole-KB network analysis for the overview: totals, actionable health
/// signals, and read-only connectivity insight. Computed in one pass over the
/// index + edges by `KnowledgeBaseService.networkStats()`.
///
/// `noteCount` and `linkCount` cover every note. Everything else — health,
/// hubs, degree, components, by-type — covers live notes only (`KBNoteKind`),
/// and the links between them; `archived` and `receipts` list the rest.
nonisolated struct KBNetworkStats: Equatable {
    let noteCount: Int
    let linkCount: Int
    let orphans: [String]                // degree 0, path asc
    let weaklyLinked: [String]           // degree exactly 1, path asc
    let dangling: [KBDanglingLink]       // source asc, then target asc
    let islands: [[String]]              // components of size >= 2 except the largest
    let topHubs: [KBHub]                 // degree desc, path asc; degree > 0; capped
    let avgDegree: Double
    let maxDegree: Int
    let byType: [KBTypeCount]            // every KBEntityGroup, in allCases order, 0s included
    let clusterCount: Int                // components with size >= 2
    let largestComponentSize: Int
    /// Notes left out of the health and insight figures, path asc.
    var archived: [String] = []
    var receipts: [String] = []

    static let empty = KBNetworkStats(
        noteCount: 0, linkCount: 0, orphans: [], weaklyLinked: [], dangling: [], islands: [],
        topHubs: [], avgDegree: 0, maxDegree: 0,
        byType: KBEntityGroup.allCases.map { KBTypeCount(group: $0, count: 0) },
        clusterCount: 0, largestComponentSize: 0)
}

extension KBNetworkStats {
    /// "avg 1.1 · max 3 connections per note".
    var degreeSummary: String {
        "avg \(String(format: "%.1f", avgDegree)) · max \(maxDegree) connections per note"
    }

    /// "1 main cluster + K islands · largest covers M notes", with the
    /// one-cluster and no-links cases worded on their own.
    var componentsSummary: String {
        switch clusterCount {
        case 0: return "No linked notes yet"
        case 1: return "1 main cluster · covers \(largestComponentSize) notes"
        default:
            let islands = clusterCount - 1
            return "1 main cluster + \(islands) island\(islands == 1 ? "" : "s")"
                + " · largest covers \(largestComponentSize) notes"
        }
    }

    /// Which items a "Show all" disclosure expands to: those after the first
    /// `topN`, at most `cap` of them. `hidden` is what's left over, so a
    /// ~1,300-item list never renders ~1,300 buttons.
    static func disclosureWindow(count: Int, topN: Int, cap: Int) -> (shown: Range<Int>, hidden: Int) {
        let start = min(topN, count)
        let end = min(count, topN + cap)
        return (start..<end, count - end)
    }

    /// "Not counted: 112 archived notes · 20 receipts", or nil when nothing
    /// was set aside.
    var setAsideSummary: String? {
        var parts: [String] = []
        if !archived.isEmpty { parts.append("\(archived.count) archived note\(archived.count == 1 ? "" : "s")") }
        if !receipts.isEmpty { parts.append("\(receipts.count) receipt\(receipts.count == 1 ? "" : "s")") }
        return parts.isEmpty ? nil : "Not counted: " + parts.joined(separator: " · ")
    }

    /// A health row's title: "Orphaned notes: ✓ none", "1 orphaned note",
    /// "5 orphaned notes".
    static func healthTitle(_ count: Int, singular: String, plural: String) -> String {
        switch count {
        case 0: return plural.prefix(1).uppercased() + plural.dropFirst() + ": ✓ none"
        case 1: return "1 \(singular)"
        default: return "\(count) \(plural)"
        }
    }
}
