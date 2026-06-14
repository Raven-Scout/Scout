import Combine
import Foundation
import SwiftUI

/// Loads `dreaming-proposals.md`, keeps it in sync via FSEvents, and publishes
/// the parsed proposals plus a pending-count for the sidebar badge.
///
/// Mirrors `ActionItemsDocumentService` but for a single fixed file (there is
/// no per-date dimension). Loading begins at app launch so the badge is
/// populated before the user ever opens the Proposals section.
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

    let fileURL: URL
    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?

    /// Number of proposals still awaiting the user's decision — the value the
    /// sidebar badge shows.
    var pendingCount: Int { proposals.filter(\.isAwaitingDecision).count }

    init(fileURL: URL, fileEvents: any FileSystemEventSource) {
        self.fileURL = fileURL
        self.fileEvents = fileEvents
    }

    /// Load (or reload) the proposals file and start watching its directory.
    func load() {
        state = .loading
        reparse()
        startWatching()
    }

    /// Re-read and re-parse the file immediately. Called by the view after a
    /// write so the UI reflects the change without waiting on FSEvents.
    func reload() { reparse() }

    private func reparse() {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            proposals = []
            state = .missing(fileURL)
            return
        }
        do {
            let text = try String(contentsOf: fileURL, encoding: .utf8)
            proposals = ProposalsParser.parse(text: text)
            state = .loaded
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    private func startWatching() {
        watchTask?.cancel()
        let directory = fileURL.deletingLastPathComponent()
        let target = fileURL.lastPathComponent
        let stream = fileEvents.events(for: directory)
        watchTask = Task { [weak self] in
            var debounce: Task<Void, Never>?
            for await event in stream {
                guard self != nil else { return }
                guard event.url.lastPathComponent == target else { continue }
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
