// Scout/PerFileItems/PerFileDocumentService.swift
import Combine
import Foundation
import SwiftUI

/// Loads per-file items from a directory, keeps them in sync via FSEvents,
/// and publishes the parsed items plus an active-count for sidebar badges.
///
/// Each `*.md` file in the directory is one item (YAML frontmatter + body).
/// Loading begins on demand; call `load()` once when the view appears.
@MainActor
final class PerFileDocumentService: ObservableObject {
    enum State: Equatable {
        case idle, loading, loaded
        case missing(URL)
        case failed(String)
    }

    @Published private(set) var items: [PerFileItem] = []
    @Published private(set) var state: State = .idle

    /// The directory scanned for item files.
    let directoryURL: URL
    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?

    /// Number of items that are still active — the value the sidebar badge shows.
    var activeCount: Int { items.filter(\.isActive).count }

    init(directoryURL: URL, fileEvents: any FileSystemEventSource) {
        self.directoryURL = directoryURL
        self.fileEvents = fileEvents
    }

    /// Load (or reload) the items directory and start watching it.
    func load() {
        state = .loading
        reparse()
        startWatching()
    }

    /// Re-scan immediately. Called by the view after a write so the UI reflects
    /// the change without waiting on FSEvents.
    func reload() { reparse() }

    private func reparse() {
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: directoryURL.path, isDirectory: &isDir),
              isDir.boolValue else {
            items = []
            state = .missing(directoryURL)
            return
        }
        let files: [URL]
        do {
            files = try FileManager.default.contentsOfDirectory(
                at: directoryURL,
                includingPropertiesForKeys: nil
            )
        } catch {
            state = .failed(error.localizedDescription)
            return
        }
        items = files
            .filter { $0.pathExtension == "md" }
            // Newest first by filename — files are named `YYYY-MM-DD-slug.md`,
            // so a reverse lexicographic sort is reverse-chronological.
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
            .compactMap { url -> PerFileItem? in
                guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
                return PerFileItemParser.parseFile(contents: text, fileURL: url)
            }
        state = .loaded
    }

    private func startWatching() {
        watchTask?.cancel()
        let stream = fileEvents.events(for: directoryURL)
        watchTask = Task { [weak self] in
            var debounce: Task<Void, Never>?
            for await event in stream {
                guard self != nil else { return }
                guard event.url.pathExtension == "md" else { continue }
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
