import Combine
import Foundation

/// The KB sidebar's "Search notes…" field: the typed query, debounced, and
/// the hits `KnowledgeBaseService.searchContent` returns for it.
///
/// The hits follow the tree, not just the query: each rebuilt index runs the
/// search again. Before, the hits were worked out once, just after typing, so a
/// query typed while the first scan was still running, or before a new note was
/// picked up, kept "No results" for a note the tree was already showing.
@MainActor
final class KBNoteSearch: ObservableObject {
    @Published var query: String = "" {
        didSet { if query != oldValue { schedule() } }
    }
    @Published private(set) var hits: [KBSearchHit] = []

    private weak var service: KnowledgeBaseService?
    private let debounce: Duration
    private var task: Task<Void, Never>?
    private var indexChanges: AnyCancellable?

    init(debounce: Duration = .milliseconds(200)) {
        self.debounce = debounce
    }

    /// Search `service`'s notes. Call once the view has its service.
    func attach(_ service: KnowledgeBaseService) {
        guard self.service !== service else { return }
        self.service = service
        // `$index` fires before the new index is stored (and after the tree,
        // which a reparse sets first); `schedule` reads it from a task, by
        // which time both are in place.
        indexChanges = service.$index.dropFirst().sink { [weak self] _ in self?.schedule() }
        schedule()
    }

    /// Await the pending search — used by tests.
    func settle() async {
        await task?.value
    }

    private func schedule() {
        task?.cancel()
        let q = query
        guard q.count >= 2, service != nil else { hits = []; task = nil; return }
        let delay = debounce
        task = Task { [weak self] in
            if delay > .zero { try? await Task.sleep(for: delay) }
            guard let self, !Task.isCancelled, let service = self.service else { return }
            self.hits = service.searchContent(q)
        }
    }
}
