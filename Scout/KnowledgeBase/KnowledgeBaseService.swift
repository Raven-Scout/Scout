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

    @Published private(set) var tree: [KBNode] = [] { didSet { generation &+= 1 } }
    @Published private(set) var state: State = .idle
    /// Wikilink graph index, rebuilt on every reparse. Powers backlinks,
    /// in-app wikilink navigation and the local graph.
    @Published private(set) var index: KBIndex = .empty { didSet { generation &+= 1 } }

    /// Bumped whenever `tree` or `index` is replaced; keys per-reparse caches.
    private var generation = 0
    private var networkStatsCache: (generation: Int, hubCap: Int, stats: KBNetworkStats)?

    /// The scout directory; the KB lives in its `knowledge-base/` subfolder.
    let scoutDirectory: URL
    /// Root scanned for the tree: `scoutDirectory/knowledge-base`.
    let kbDirectory: URL

    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?
    private var reparseTask: Task<Void, Never>?

    /// Directory entries never surfaced in the tree. `nonisolated`: read by the
    /// detached tree build in `buildChildren`.
    private nonisolated static let ignoredNames: Set<String> = [
        ".git", ".obsidian", ".scout-cache", ".scout-logs", ".scout-state",
        "node_modules", ".DS_Store",
    ]
    /// File extensions the tree shows (and the editor can open). `nonisolated`:
    /// read by the detached tree build in `buildChildren`.
    private nonisolated static let visibleExtensions: Set<String> = ["md", "yaml", "yml"]

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

    private nonisolated enum ReparseOutcome {
        case missing
        case failed(String)
        case loaded([KBNode], KBIndex, KBNetworkStats)
    }

    /// Rebuild tree + index. The directory walk and the per-note reads run off
    /// the main actor (a few hundred notes means hundreds of disk reads — doing
    /// that on the main actor on every FSEvent froze the UI, same class of
    /// problem as the activity-heatmap fix); only publishing hops back to main.
    private func reparse() {
        reparseTask?.cancel()
        let kbDir = kbDirectory
        let scoutDir = scoutDirectory
        reparseTask = Task { [weak self] in
            let result = await Task.detached(priority: .userInitiated) { () -> ReparseOutcome in
                var isDir: ObjCBool = false
                guard FileManager.default.fileExists(atPath: kbDir.path, isDirectory: &isDir),
                      isDir.boolValue else { return .missing }
                // Distinguish "directory exists but can't be read" (permissions,
                // sandbox) from a genuinely empty knowledge base.
                do { _ = try FileManager.default.contentsOfDirectory(atPath: kbDir.path) }
                catch { return .failed(error.localizedDescription) }
                let tree = KnowledgeBaseService.buildChildren(of: kbDir, scoutDirectory: scoutDir)
                let index = KnowledgeBaseService.buildIndex(tree: tree, scoutDirectory: scoutDir)
                let stats = KnowledgeBaseService.computeNetworkStats(
                    tree: tree, index: index, hubCap: KnowledgeBaseService.defaultHubCap)
                return .loaded(tree, index, stats)
            }.value
            guard let self, !Task.isCancelled else { return }
            switch result {
            case .loaded(let tree, let index, let stats):
                self.tree = tree
                self.index = index
                // Seed the cache after both bumps, so the overview's first
                // read after a reparse is a lookup, not a main-actor pass.
                self.networkStatsCache = (self.generation, Self.defaultHubCap, stats)
                self.state = .loaded
            case .missing:
                self.tree = []
                self.index = .empty
                self.state = .missing(kbDir)
            case .failed(let message):
                self.tree = []
                self.index = .empty
                self.state = .failed(message)
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

    /// Lowercased names of every non-hidden file under `scoutDirectory`
    /// (`.md` by stem, others by full name), skipping `ignoredNames` and the
    /// KB's own markdown notes — those are the resolver's job, and listing
    /// them would hide every resolver miss whose stem exists in the KB.
    /// Filenames only — no file is read. Refreshed on KB reparses only, so a
    /// vault file added outside the KB shows up after the next KB change.
    nonisolated static func vaultFileNames(under scoutDirectory: URL,
                                           skippingMarkdownIn kbDirectory: URL) -> Set<String> {
        guard let walker = FileManager.default.enumerator(
            at: scoutDirectory, includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles, .skipsPackageDescendants]) else { return [] }
        let kbPrefix = kbDirectory.standardizedFileURL.path + "/"
        var names = Set<String>()
        while let url = walker.nextObject() as? URL {
            if ignoredNames.contains(url.lastPathComponent) { walker.skipDescendants(); continue }
            guard (try? url.resourceValues(forKeys: [.isRegularFileKey]))?.isRegularFile == true else { continue }
            let isMarkdown = url.pathExtension.lowercased() == "md"
            if isMarkdown && url.standardizedFileURL.path.hasPrefix(kbPrefix) { continue }
            let name = url.lastPathComponent.lowercased()
            names.insert(isMarkdown ? (name as NSString).deletingPathExtension : name)
        }
        return names
    }

    // MARK: - Graph index

    /// Build the wikilink index from the current tree: a stem→path map, each
    /// file's outgoing link targets, and the note text itself. Reads every note
    /// exactly once (off the main actor, via `reparse`); the cached text serves
    /// backlink excerpts and full-text search without further disk I/O.
    nonisolated static func buildIndex(tree: [KBNode], scoutDirectory: URL) -> KBIndex {
        let files = tree.flatMap(\.allFiles).filter { $0.ext == "md" }
        var stemPaths: [String: [String]] = [:]
        var outByFile: [String: [String]] = [:]
        var textByFile: [String: String] = [:]
        for file in files {
            stemPaths[file.displayName.lowercased(), default: []].append(file.relativePath)
        }
        var typeByFile: [String: String] = [:]
        for file in files {
            guard let text = try? String(contentsOf: file.url, encoding: .utf8) else { continue }
            textByFile[file.relativePath] = text
            outByFile[file.relativePath] = extractWikilinks(text)
            typeByFile[file.relativePath] = frontmatterType(text)
        }
        return KBIndex(stemPaths: stemPaths, outByFile: outByFile,
                       textByFile: textByFile, typeByFile: typeByFile,
                       vaultNames: vaultFileNames(under: scoutDirectory,
                                                  skippingMarkdownIn: scoutDirectory.appendingPathComponent("knowledge-base")))
    }

    /// The `type:` value from a note's leading YAML frontmatter (lowercased),
    /// or nil. This is the ontology entity type the plugin writes — the
    /// authoritative signal for graph grouping, ahead of path heuristics.
    nonisolated static func frontmatterType(_ text: String) -> String? {
        let lines = text.components(separatedBy: "\n")
        guard lines.first?.trimmingCharacters(in: .whitespaces) == "---" else { return nil }
        for line in lines.dropFirst() {
            let t = line.trimmingCharacters(in: .whitespaces)
            if t == "---" { break }
            if t.hasPrefix("type:") {
                let value = t.dropFirst("type:".count).trimmingCharacters(in: .whitespaces).lowercased()
                return value.isEmpty ? nil : value
            }
        }
        return nil
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

    /// Resolve a wikilink target (e.g. `atlas`, `people/atlas`, `atlas#Notes`,
    /// possibly with spaces) to a repo-relative path, or nil if no matching
    /// note exists. See `KBIndex.resolve(_:from:)`.
    func resolveWikilink(_ target: String) -> String? {
        index.resolve(target)
    }

    /// Outgoing links of a note, each with its resolved target (nil = no note;
    /// see `networkStats()` for which of those count as dangling).
    func outgoingLinks(for relPath: String) -> [KBLink] {
        let targets = index.outByFile[relPath] ?? []
        let resolved = index.resolvedOut[relPath] ?? []
        return zip(targets, resolved).map { KBLink(target: $0, resolved: $1) }
    }

    /// Notes that link to `relPath`, with a one-line excerpt around the link.
    /// Serves everything from the index — no disk reads.
    func backlinks(for relPath: String) -> [KBBacklink] {
        var results: [KBBacklink] = []
        for (from, resolved) in index.resolvedOut where from != relPath && resolved.contains(relPath) {
            let excerpt = excerpt(in: index.textByFile[from] ?? "", from: from, linkingTo: relPath)
            results.append(KBBacklink(path: from,
                                      name: KBNode.displayName(forPath: from),
                                      excerpt: excerpt))
        }
        return results.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    /// The first line of `text` holding a wikilink that resolves to `target`,
    /// trimmed to 140 characters — exact, so a URL or a same-named note's
    /// link elsewhere in the note can't be picked instead.
    private func excerpt(in text: String, from source: String, linkingTo target: String) -> String {
        let line = text.components(separatedBy: "\n").first { line in
            line.contains("[[") && Self.extractWikilinks(line).contains { index.resolve($0, from: source) == target }
        }
        return (line ?? "").trimmingCharacters(in: .whitespaces).prefix(140).description
    }

    /// Unique undirected wikilink edges across the whole KB, precomputed off
    /// the main actor when the index is built.
    private func undirectedEdges() -> Set<KBGraphEdge> {
        index.edges
    }

    /// Undirected adjacency over `edges`. Shared by `localGraph(around:)` and
    /// `networkStats()` so "degree" means the same thing in both.
    nonisolated static func adjacency(of edges: Set<KBGraphEdge>) -> [String: Set<String>] {
        var adj: [String: Set<String>] = [:]
        for e in edges {
            adj[e.from, default: []].insert(e.to)
            adj[e.to, default: []].insert(e.from)
        }
        return adj
    }

    /// Build the local subgraph centred on `relPath`: BFS over undirected
    /// wikilink edges out to `depth` hops, capped at `maxNodes` by degree.
    func localGraph(around relPath: String, depth: Int = 2, maxNodes: Int = 26) -> KBGraph {
        let edgeSet = undirectedEdges()
        let adj = Self.adjacency(of: edgeSet)
        // BFS from center.
        var visited: Set<String> = [relPath]
        var frontier: [String] = [relPath]
        for _ in 0..<max(0, depth) {
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
                        group: KBEntityGroup.of(path, type: index.typeByFile[path]),
                        degree: degree(path),
                        isCenter: path == relPath)
        }
        let edges = edgeSet.filter { keptSet.contains($0.from) && keptSet.contains($0.to) }
        return KBGraph(nodes: nodes, edges: Array(edges))
    }

    /// Count of notes and unique links across the whole KB (for the overview).
    func graphStats() -> (notes: Int, links: Int) {
        let notes = tree.flatMap(\.allFiles).filter { $0.ext == "md" }.count
        return (notes, undirectedEdges().count)
    }

    /// The whole-KB graph: every markdown note plus the unique wikilink edges
    /// between them. Feeds the global graph on the overview.
    func fullGraph() -> KBGraph {
        let edgeSet = undirectedEdges()
        var degree: [String: Int] = [:]
        for e in edgeSet { degree[e.from, default: 0] += 1; degree[e.to, default: 0] += 1 }
        let nodes = tree.flatMap(\.allFiles).filter { $0.ext == "md" }.map { file in
            KBGraphNode(id: file.relativePath, label: file.displayName,
                        group: KBEntityGroup.of(file.relativePath, type: index.typeByFile[file.relativePath]),
                        degree: degree[file.relativePath] ?? 0, isCenter: false)
        }
        return KBGraph(nodes: nodes, edges: Array(edgeSet))
    }

    /// The overview's default seed: the most-connected notes across the whole
    /// KB, bounded so the map opens readable rather than as a full-vault
    /// hairball. Degree/grouping come from `fullGraph()`.
    func hubGraph(maxNodes: Int = 40) -> KBGraph {
        fullGraph().topHubs(maxNodes: maxNodes)
    }

    /// The overview's default hub cap; `reparse` precomputes stats for it.
    nonisolated static let defaultHubCap = 20

    /// One-pass network analysis for the overview, cached until the next
    /// reparse (the overview evaluates it on every body pass). The default
    /// cap is computed off the main actor during the reparse itself.
    func networkStats(hubCap: Int = KnowledgeBaseService.defaultHubCap) -> KBNetworkStats {
        if let c = networkStatsCache, c.generation == generation, c.hubCap == hubCap { return c.stats }
        let stats = Self.computeNetworkStats(tree: tree, index: index, hubCap: hubCap)
        networkStatsCache = (generation, hubCap, stats)
        return stats
    }

    /// Totals, orphans / weakly-linked / dangling / islands (health) and
    /// hubs / degree / per-type / components (insight), in one pass over the
    /// index's precomputed resolution and edges — pure, so `reparse` runs it
    /// off the main actor.
    nonisolated static func computeNetworkStats(tree: [KBNode], index: KBIndex, hubCap: Int) -> KBNetworkStats {
        let notes = tree.flatMap(\.allFiles).filter { $0.ext == "md" }.map(\.relativePath)
        guard !notes.isEmpty else { return .empty }
        let edgeSet = index.edges
        let adj = adjacency(of: edgeSet)
        let degree: (String) -> Int = { adj[$0]?.count ?? 0 }

        // Health: orphans / weakly-linked.
        let orphans = notes.filter { degree($0) == 0 }.sorted()
        let weaklyLinked = notes.filter { degree($0) == 1 }.sorted()

        // Health: dangling links — targets that are truly missing. Ticket ids
        // and files elsewhere in the vault (which Obsidian resolves) don't
        // count. `extractWikilinks` already de-dups targets per note, so each
        // (source, target) appears once.
        var dangling: [KBDanglingLink] = []
        for (source, targets) in index.outByFile {
            let resolved = index.resolvedOut[source] ?? []
            for (t, to) in zip(targets, resolved) where to == nil
                && !KBIndex.isTicketID(t) && !index.existsInVault(t) {
                dangling.append(KBDanglingLink(source: source, target: t))
            }
        }
        dangling.sort { $0.source != $1.source ? $0.source < $1.source : $0.target < $1.target }

        // Connected components (iterative DFS). Each is sorted, then ranked by
        // size desc and first path asc so the mainland is stable on ties.
        var seen = Set<String>()
        var components: [[String]] = []
        for start in notes where !seen.contains(start) {
            var comp: [String] = []
            var stack = [start]
            seen.insert(start)
            while let node = stack.popLast() {
                comp.append(node)
                for nb in adj[node] ?? [] where !seen.contains(nb) {
                    seen.insert(nb)
                    stack.append(nb)
                }
            }
            components.append(comp.sorted())
        }
        components.sort { $0.count != $1.count ? $0.count > $1.count : $0[0] < $1[0] }
        let clusters = components.filter { $0.count >= 2 }

        // Insight: degree summary, hubs, per-type.
        let topHubs = notes
            .filter { degree($0) > 0 }
            .sorted { degree($0) != degree($1) ? degree($0) > degree($1) : $0 < $1 }
            .prefix(hubCap)
            .map { KBHub(path: $0, degree: degree($0)) }
        var counts: [KBEntityGroup: Int] = [:]
        for n in notes { counts[KBEntityGroup.of(n, type: index.typeByFile[n]), default: 0] += 1 }

        return KBNetworkStats(
            noteCount: notes.count,
            linkCount: edgeSet.count,
            orphans: orphans,
            weaklyLinked: weaklyLinked,
            dangling: dangling,
            islands: Array(clusters.dropFirst()),
            topHubs: Array(topHubs),
            avgDegree: Double(2 * edgeSet.count) / Double(notes.count),
            maxDegree: notes.map(degree).max() ?? 0,
            byType: KBEntityGroup.allCases.map { KBTypeCount(group: $0, count: counts[$0] ?? 0) },
            clusterCount: clusters.count,
            largestComponentSize: components.first?.count ?? 0)
    }

    /// Full-text search across note names and contents (from the index's cached
    /// text — no disk reads), returning a snippet for the first matching line.
    /// Capped at 30 hits.
    func searchContent(_ query: String) -> [KBSearchHit] {
        let q = query.lowercased()
        guard q.count >= 2 else { return [] }

        // A query that is exactly a tag (typed, or arrived from a chip click)
        // matches tag occurrences, not substrings — otherwise `#RELONE` also
        // reports every `#RELONEX` note, and tags are short enough that the
        // collisions are common.
        let tag = KBTag.normalized(query.trimmingCharacters(in: .whitespaces))
        let matches: (String) -> Bool = if let tag {
            { KBTag.tags(in: $0).contains(tag) }
        } else {
            { $0.lowercased().contains(q) }
        }

        var hits: [KBSearchHit] = []
        for file in tree.flatMap(\.allFiles) where file.ext == "md" {
            // A tag query is about content, so don't let a filename that merely
            // contains the letters masquerade as a hit.
            let nameMatch = tag == nil && (file.displayName.lowercased().contains(q)
                || file.relativePath.lowercased().contains(q))
            guard let text = index.textByFile[file.relativePath] else {
                if nameMatch {
                    hits.append(KBSearchHit(path: file.relativePath, name: file.displayName, snippet: ""))
                }
                continue
            }
            if let line = text.components(separatedBy: "\n").first(where: matches) {
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
