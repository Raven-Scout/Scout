import Combine
import Foundation

/// The KB sidebar's "Search notes…" field: the typed query, debounced, and
/// the hits `KnowledgeBaseService.searchContent` returns for it.
///
/// The hits follow the tree, not just the query: each rebuilt index runs the
/// search again. Before, the hits were worked out once, just after typing, so a
/// query typed while the first scan was still running, or before a new note was
/// picked up, kept "No results" for a note the tree was already showing.
///
/// Typing and rebuilt indexes share one debounce, so a burst of either (a
/// scheduled run writing notes rescans about every 250 ms) runs one search,
/// and the search itself runs off the main actor over a snapshot of the tree
/// and index. Only the newest search's hits land.
@MainActor
final class KBNoteSearch: ObservableObject {
    typealias Sleep = @Sendable (Duration) async throws -> Void

    @Published var query: String = "" {
        didSet { if query != oldValue { schedule() } }
    }
    @Published private(set) var hits: [KBSearchHit] = []
    /// Searches that ran to completion (tests check the debounce with it).
    private(set) var searchesRun = 0

    private weak var service: KnowledgeBaseService?
    private let debounce: Duration
    private let sleep: Sleep
    /// The newest scheduled search; an older one that finishes late is dropped.
    private var latest = 0
    /// Searches still running, so `settle` can wait for all of them.
    private var running: [Int: Task<Void, Never>] = [:]
    private var indexChanges: AnyCancellable?

    init(debounce: Duration = .milliseconds(200),
         sleep: @escaping Sleep = { try await Task.sleep(for: $0) }) {
        self.debounce = debounce
        self.sleep = sleep
    }

    /// Search `service`'s notes. Call once the view has its service.
    func attach(_ service: KnowledgeBaseService) {
        guard self.service !== service else { return }
        self.service = service
        // `$index` fires before the new index is stored (and after the tree,
        // which a reparse sets first); the search reads both after the
        // debounce, by which time both are in place. No query, no work.
        indexChanges = service.$index.dropFirst().sink { [weak self] _ in
            guard let self, Self.isSearchable(self.query) else { return }
            self.schedule()
        }
        schedule()
    }

    /// Wait for every scheduled search to finish — used by tests.
    func settle() async {
        while let task = running.values.first { await task.value }
    }

    private static func isSearchable(_ query: String) -> Bool { query.count >= 2 }

    private func schedule() {
        latest += 1
        let id = latest
        running.values.forEach { $0.cancel() }
        guard Self.isSearchable(query), service != nil else {
            if !hits.isEmpty { hits = [] }
            return
        }
        let (delay, sleep) = (debounce, self.sleep)
        running[id] = Task { [weak self] in
            defer { self?.running[id] = nil }
            if delay > .zero { try? await sleep(delay) }
            guard let self, !Task.isCancelled, id == self.latest, let service = self.service else { return }
            let (q, tree, index) = (self.query, service.tree, service.index)
            let found = await Task.detached(priority: .userInitiated) {
                KnowledgeBaseService.searchContent(q, tree: tree, index: index)
            }.value
            self.searchesRun += 1
            guard !Task.isCancelled, id == self.latest else { return }
            self.hits = found
        }
    }
}
