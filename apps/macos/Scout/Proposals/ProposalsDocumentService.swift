import Combine
import Foundation
import SwiftUI

/// Loads per-file proposals from the `dreaming-proposals/` directory, keeps them
/// in sync via FSEvents, and publishes the parsed proposals plus a pending-count
/// for the sidebar badge.
///
/// Each `*.md` file in the directory is one proposal (YAML frontmatter + body);
/// the sibling `dreaming-proposals.md` index file is intentionally ignored — it
/// has no frontmatter, so the parser skips it. Loading begins at app launch so
/// the badge is populated before the user ever opens the Proposals section.
@MainActor
final class ProposalsDocumentService: ObservableObject {
    enum State: Equatable {
        case idle
        case loading
        case loaded
        case missing(URL)
        case failed(String)
    }

    @Published private(set) var proposals: [Proposal] = []
    @Published private(set) var state: State = .idle

    /// The directory scanned for proposal files (e.g. `~/Scout/dreaming-proposals`).
    let directoryURL: URL
    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?

    /// Number of proposals still awaiting the user's decision — the value the
    /// sidebar badge shows.
    var pendingCount: Int { proposals.filter(\.isAwaitingDecision).count }

    init(directoryURL: URL, fileEvents: any FileSystemEventSource) {
        self.directoryURL = directoryURL
        self.fileEvents = fileEvents
    }

    /// Load (or reload) the proposals directory and start watching it.
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
            proposals = []
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
        let parsed = files
            .filter { $0.pathExtension == "md" }
            // Newest first by filename — files are named `YYYY-MM-DD-slug.md`,
            // so a reverse lexicographic sort is reverse-chronological.
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
            .compactMap { url -> Proposal? in
                guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
                return ProposalsParser.parseFile(contents: text, fileURL: url)
            }
        proposals = parsed
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
