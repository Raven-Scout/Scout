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

    /// The scout directory; the KB lives in its `knowledge-base/` subfolder.
    let scoutDirectory: URL
    /// Root scanned for the tree: `scoutDirectory/knowledge-base`.
    let kbDirectory: URL

    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?

    /// Directory entries never surfaced in the tree.
    private static let ignoredNames: Set<String> = [
        ".git", ".obsidian", ".scout-cache", ".scout-logs", ".scout-state",
        "node_modules", ".DS_Store",
    ]
    /// File extensions the tree shows (and the editor can open).
    private static let visibleExtensions: Set<String> = ["md", "yaml", "yml"]

    init(scoutDirectory: URL, fileEvents: any FileSystemEventSource) {
        self.scoutDirectory = scoutDirectory
        self.kbDirectory = scoutDirectory.appendingPathComponent("knowledge-base")
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

    // MARK: - Tree building

    private func reparse() {
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: kbDirectory.path, isDirectory: &isDir),
              isDir.boolValue else {
            tree = []
            state = .missing(kbDirectory)
            return
        }
        tree = Self.buildChildren(of: kbDirectory, scoutDirectory: scoutDirectory)
        state = .loaded
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

    deinit { watchTask?.cancel() }
}
