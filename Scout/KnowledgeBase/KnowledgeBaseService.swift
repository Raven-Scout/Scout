import Combine
import Foundation
import SwiftUI

/// Builds and maintains the knowledge-base file tree under
/// `~/Scout/knowledge-base/`, keeping it in sync via FSEvents.
///
/// Mirrors `PerFileDocumentService`'s lifecycle (load-on-appear, debounced
/// reparse on file events) but produces a recursive `KBNode` tree rather than a
/// flat item list, since the KB is an arbitrarily-nested folder of `.md`/`.yaml`
/// notes the user browses and edits in place.
@MainActor
final class KnowledgeBaseService: ObservableObject {
    enum State: Equatable {
        case idle, loading, loaded
        case missing(URL)
        case failed(String)
    }

    @Published private(set) var tree: [KBNode] = []
    @Published private(set) var state: State = .idle
    /// Wikilink graph index, rebuilt on every reparse. Powers backlinks,
    /// in-app wikilink navigation and the local graph.
    @Published private(set) var index: KBIndex = .empty

    /// The scout directory; the KB lives in its `knowledge-base/` subfolder.
    let scoutDirectory: URL
    /// Root scanned for the tree: `scoutDirectory/knowledge-base`.
    let kbDirectory: URL

    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?
    private var reparseTask: Task<Void, Never>?

    /// Directory entries never surfaced in the tree.
    private static let ignoredNames: Set<String> = [
        ".git", ".obsidian", ".scout-cache", ".scout-logs", ".scout-state",
        "node_modules", ".DS_Store",
    ]
    /// File extensions the tree shows (and the editor can open).
    private static let visibleExtensions: Set<String> = ["md", "yaml", "yml"]

    init(scoutDirectory: URL, fileEvents: any FileSystemEventSource) {
        // `contentsOfDirectory(at:)` returns symlink-resolved file URLs, so
        // resolve the root too. Otherwise, when ~/Scout is a symlink, the tree's
        // node URLs (real path) and this root (symlink path) mismatch — breaking
        // relative-path stripping and the writer's in-KB guard ("people.md is
        // outside the knowledge base").
        let resolved = scoutDirectory.resolvingSymlinksInPath()
        self.scoutDirectory = resolved
        self.kbDirectory = resolved.appendingPathComponent("knowledge-base")
        self.fileEvents = fileEvents
    }

    /// Build the tree and start watching. Call once when the view appears.
    func load() {
        state = .loading
        reparse()
        startWatching()
    }

    /// Re-scan immediately — called by the view after a write (create/delete/
    /// rename) so the tree reflects the change without waiting on FSEvents.
    func reload() { reparse() }

    /// Read a file's full text contents, or nil if unreadable.
    func readFile(_ url: URL) -> String? {
        try? String(contentsOf: url, encoding: .utf8)
    }

    /// Await the currently scheduled reparse — used by tests (and anyone who
    /// needs the tree/index to reflect disk right now).
    func reparseAndWait() async {
        reparse()
        await reparseTask?.value
    }

    // MARK: - Tree building

    /// Rebuild tree + index. The directory walk and the per-note reads run off
    /// the main actor (a few hundred notes means hundreds of disk reads — doing
    /// that on the main actor on every FSEvent froze the UI, same class of
    /// problem as the activity-heatmap fix); only publishing hops back to main.
    private func reparse() {
        reparseTask?.cancel()
        let kbDir = kbDirectory
        let scoutDir = scoutDirectory
        reparseTask = Task { [weak self] in
            let result = await Task.detached(priority: .userInitiated) { () -> (tree: [KBNode], index: KBIndex)? in
                var isDir: ObjCBool = false
                guard FileManager.default.fileExists(atPath: kbDir.path, isDirectory: &isDir),
                      isDir.boolValue else { return nil }
                let tree = KnowledgeBaseService.buildChildren(of: kbDir, scoutDirectory: scoutDir)
                let index = KnowledgeBaseService.buildIndex(tree: tree, scoutDirectory: scoutDir)
                return (tree, index)
            }.value
            guard let self, !Task.isCancelled else { return }
            if let result {
                self.tree = result.tree
                self.index = result.index
                self.state = .loaded
            } else {
                self.tree = []
                self.index = .empty
                self.state = .missing(kbDir)
            }
        }
    }

    /// Recursively build the sorted child nodes of `directory`. Directories sort
    /// before files; both alphabetically (case-insensitive). Empty directories
    /// (no visible descendants) are pruned so the tree stays readable.
    nonisolated static func buildChildren(of directory: URL, scoutDirectory: URL) -> [KBNode] {
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }

        var dirs: [KBNode] = []
        var files: [KBNode] = []

        for url in entries {
            let name = url.lastPathComponent
            if ignoredNames.contains(name) { continue }

            let isDirectory = (try? url.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory ?? false
            let rel = relativePath(of: url, in: scoutDirectory)

            if isDirectory {
                let children = buildChildren(of: url, scoutDirectory: scoutDirectory)
                guard !children.isEmpty else { continue } // prune empties
                dirs.append(KBNode(kind: .directory, url: url, relativePath: rel,
                                   name: name, ext: "", children: children))
            } else {
                let ext = url.pathExtension.lowercased()
                guard visibleExtensions.contains(ext) else { continue }
                files.append(KBNode(kind: .file, url: url, relativePath: rel,
                                    name: name, ext: ext, children: []))
            }
        }

        let byName: (KBNode, KBNode) -> Bool = {
            $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
        return dirs.sorted(by: byName) + files.sorted(by: byName)
    }

    /// Path of `url` relative to `scoutDirectory`, or the last component if it
    /// somehow lies outside (shouldn't happen for KB files).
    nonisolated static func relativePath(of url: URL, in scoutDirectory: URL) -> String {
        let full = url.standardizedFileURL.path
        let prefix = scoutDirectory.standardizedFileURL.path + "/"
        return full.hasPrefix(prefix) ? String(full.dropFirst(prefix.count)) : url.lastPathComponent
    }

    // MARK: - Graph index

    /// Build the wikilink index from the current tree: a stem→path map, each
    /// file's outgoing link targets, and the note text itself. Reads every note
    /// exactly once (off the main actor, via `reparse`); the cached text serves
    /// backlink excerpts and full-text search without further disk I/O.
    nonisolated static func buildIndex(tree: [KBNode], scoutDirectory: URL) -> KBIndex {
        let files = tree.flatMap(\.allFiles).filter { $0.ext == "md" }
        var stemToPath: [String: String] = [:]
        var outByFile: [String: [String]] = [:]
        var textByFile: [String: String] = [:]
        for file in files {
            stemToPath[file.displayName.lowercased()] = file.relativePath
        }
        for file in files {
            guard let text = try? String(contentsOf: file.url, encoding: .utf8) else { continue }
            textByFile[file.relativePath] = text
            outByFile[file.relativePath] = extractWikilinks(text)
        }
        return KBIndex(stemToPath: stemToPath, outByFile: outByFile, textByFile: textByFile)
    }

    /// Extract `[[target]]` / `[[target|alias]]` link targets (the part before
    /// `|`), de-duplicated, preserving original case and first-seen order.
    /// The alias separator may be escaped as `\|` — the form the KB writes
    /// inside table cells so the pipe isn't taken as a column break.
    nonisolated static func extractWikilinks(_ text: String) -> [String] {
        guard let re = try? NSRegularExpression(pattern: #"\[\[([^\]|]+?)(?:\\?\|[^\]]+)?\]\]"#) else { return [] }
        let ns = text as NSString
        var seen = Set<String>()
        var result: [String] = []
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            let raw = ns.substring(with: m.range(at: 1)).trimmingCharacters(in: .whitespaces)
            if !raw.isEmpty, seen.insert(raw.lowercased()).inserted { result.append(raw) }
        }
        return result
    }

    /// Resolve a wikilink target (e.g. `atlas`, possibly with spaces) to a
    /// repo-relative path, or nil if no matching note exists.
    func resolveWikilink(_ target: String) -> String? {
        index.stemToPath[target.lowercased()]
    }

    /// Outgoing links of a note, each with its resolved target (nil = dangling).
    func outgoingLinks(for relPath: String) -> [KBLink] {
        (index.outByFile[relPath] ?? []).map {
            KBLink(target: $0, resolved: index.stemToPath[$0.lowercased()])
        }
    }

    /// Notes that link to `relPath`, with a one-line excerpt around the link.
    /// Serves everything from the index — no disk reads.
    func backlinks(for relPath: String) -> [KBBacklink] {
        let targetStem = (KBNode.displayName(forPath: relPath)).lowercased()
        var results: [KBBacklink] = []
        for (from, targets) in index.outByFile {
            guard from != relPath else { continue }
            guard targets.contains(where: { index.stemToPath[$0.lowercased()] == relPath }) else { continue }
            let excerpt = Self.excerpt(in: index.textByFile[from] ?? "", mentioning: targetStem)
            results.append(KBBacklink(path: from,
                                      name: KBNode.displayName(forPath: from),
                                      excerpt: excerpt))
        }
        return results.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    private static func excerpt(in text: String, mentioning stem: String) -> String {
        let needle = "[[" + stem
        let line = text.components(separatedBy: "\n")
            .first { $0.lowercased().contains(needle) }
        return (line ?? "").trimmingCharacters(in: .whitespaces).prefix(140).description
    }

    /// Build the local subgraph centred on `relPath`: BFS over undirected
    /// wikilink edges out to `depth` hops, capped at `maxNodes` by degree.
    func localGraph(around relPath: String, depth: Int = 2, maxNodes: Int = 26) -> KBGraph {
        // Full undirected edge set.
        var edgeSet = Set<KBGraphEdge>()
        for (from, targets) in index.outByFile {
            for t in targets {
                guard let to = index.stemToPath[t.lowercased()], to != from else { continue }
                // Normalize direction so duplicates collapse.
                let (a, b) = from < to ? (from, to) : (to, from)
                edgeSet.insert(KBGraphEdge(from: a, to: b))
            }
        }
        // Adjacency.
        var adj: [String: Set<String>] = [:]
        for e in edgeSet {
            adj[e.from, default: []].insert(e.to)
            adj[e.to, default: []].insert(e.from)
        }
        // BFS from center.
        var visited: Set<String> = [relPath]
        var frontier: [String] = [relPath]
        for _ in 0..<max(1, depth) {
            var next: [String] = []
            for node in frontier {
                for n in adj[node] ?? [] where !visited.contains(n) {
                    visited.insert(n); next.append(n)
                }
            }
            frontier = next
        }
        // Degree across the full graph.
        let degree: (String) -> Int = { adj[$0]?.count ?? 0 }

        // Cap: always keep the center, then the highest-degree neighbours.
        var kept = Array(visited)
        if kept.count > maxNodes {
            kept = [relPath] + kept.filter { $0 != relPath }
                .sorted { degree($0) > degree($1) }
                .prefix(maxNodes - 1)
        }
        let keptSet = Set(kept)

        let nodes = kept.map { path in
            KBGraphNode(id: path,
                        label: KBNode.displayName(forPath: path),
                        group: KBEntityGroup.of(path),
                        degree: degree(path),
                        isCenter: path == relPath)
        }
        let edges = edgeSet.filter { keptSet.contains($0.from) && keptSet.contains($0.to) }
        return KBGraph(nodes: nodes, edges: Array(edges))
    }

    /// Count of notes and unique links across the whole KB (for the overview).
    func graphStats() -> (notes: Int, links: Int) {
        let notes = tree.flatMap(\.allFiles).filter { $0.ext == "md" }.count
        var edgeSet = Set<KBGraphEdge>()
        for (from, targets) in index.outByFile {
            for t in targets {
                guard let to = index.stemToPath[t.lowercased()], to != from else { continue }
                let (a, b) = from < to ? (from, to) : (to, from)
                edgeSet.insert(KBGraphEdge(from: a, to: b))
            }
        }
        return (notes, edgeSet.count)
    }

    /// The whole-KB graph: every markdown note plus the unique wikilink edges
    /// between them. Feeds the global graph on the overview.
    func fullGraph() -> KBGraph {
        var edgeSet = Set<KBGraphEdge>()
        for (from, targets) in index.outByFile {
            for t in targets {
                guard let to = index.stemToPath[t.lowercased()], to != from else { continue }
                let (a, b) = from < to ? (from, to) : (to, from)
                edgeSet.insert(KBGraphEdge(from: a, to: b))
            }
        }
        var degree: [String: Int] = [:]
        for e in edgeSet { degree[e.from, default: 0] += 1; degree[e.to, default: 0] += 1 }
        let nodes = tree.flatMap(\.allFiles).filter { $0.ext == "md" }.map { file in
            KBGraphNode(id: file.relativePath, label: file.displayName,
                        group: KBEntityGroup.of(file.relativePath),
                        degree: degree[file.relativePath] ?? 0, isCenter: false)
        }
        return KBGraph(nodes: nodes, edges: Array(edgeSet))
    }

    /// Full-text search across note names and contents (from the index's cached
    /// text — no disk reads), returning a snippet for the first matching line.
    /// Capped at 30 hits.
    func searchContent(_ query: String) -> [KBSearchHit] {
        let q = query.lowercased()
        guard q.count >= 2 else { return [] }
        var hits: [KBSearchHit] = []
        for file in tree.flatMap(\.allFiles) where file.ext == "md" {
            let nameMatch = file.displayName.lowercased().contains(q)
                || file.relativePath.lowercased().contains(q)
            guard let text = index.textByFile[file.relativePath] else {
                if nameMatch {
                    hits.append(KBSearchHit(path: file.relativePath, name: file.displayName, snippet: ""))
                }
                continue
            }
            if let line = text.components(separatedBy: "\n").first(where: { $0.lowercased().contains(q) }) {
                hits.append(KBSearchHit(path: file.relativePath, name: file.displayName,
                                        snippet: line.trimmingCharacters(in: .whitespaces).prefix(120).description))
            } else if nameMatch {
                hits.append(KBSearchHit(path: file.relativePath, name: file.displayName, snippet: ""))
            }
            if hits.count >= 30 { break }
        }
        return hits
    }

    // MARK: - Watching

    private func startWatching() {
        watchTask?.cancel()
        let stream = fileEvents.events(for: kbDirectory)
        watchTask = Task { [weak self] in
            var debounce: Task<Void, Never>?
            for await _ in stream {
                guard self != nil else { return }
                debounce?.cancel()
                debounce = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: 250_000_000)
                    self?.reparse()
                }
            }
        }
    }

    deinit {
        watchTask?.cancel()
        reparseTask?.cancel()
    }
}
