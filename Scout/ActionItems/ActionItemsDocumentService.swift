import Combine
import Foundation
import SwiftUI

@MainActor
final class ActionItemsDocumentService: ObservableObject {
    enum State: Equatable {
        case idle
        case loading(Date)
        case loaded(ActionItemsDocument)
        case missing(date: Date, expectedURL: URL)
        case failed(Error)

        static func == (lhs: State, rhs: State) -> Bool {
            switch (lhs, rhs) {
            case (.idle, .idle): return true
            case (.loading(let a), .loading(let b)): return a == b
            case (.loaded(let a), .loaded(let b)): return a == b
            case (.missing(let a, let au), .missing(let b, let bu)): return a == b && au == bu
            case (.failed, .failed): return true
            default: return false
            }
        }
    }

    @Published private(set) var state: State = .idle

    private let directory: URL
    private let fileEvents: any FileSystemEventSource
    private var currentDate: Date?
    private var watchTask: Task<Void, Never>?

    /// Bumped on every reparse request. A parse now runs off the main actor, so
    /// two can overlap — a slow day switching to a fast one, or a write landing
    /// mid-load. The result of a parse whose generation is no longer current is
    /// discarded, otherwise the stale one finishes last and wins.
    private var generation: UInt64 = 0

    init(directory: URL, fileEvents: any FileSystemEventSource) {
        self.directory = directory
        self.fileEvents = fileEvents
    }

    /// Load the action-items file for ``date`` (ET-local). Starts (or restarts)
    /// the FSEvents subscription filtered to that date's filename.
    func load(date: Date) async throws {
        currentDate = date
        publish(.loading(date))
        await reparse(url: url(for: date))
        startWatching()
    }

    /// Recompute the displayed document for the currently-loaded date. Called
    /// by the view after a successful CLI invocation so the user sees the
    /// change ASAP even if FSEvents is briefly laggy — the file watcher then
    /// fires for the same write and reparses again, which the equality gate in
    /// ``publish(_:)`` absorbs.
    /// `async` because the parse it drives is: callers that need to observe the
    /// outcome — including the `#47` guarantee that a failed reparse surfaces
    /// as `.failed` rather than leaving stale `.loaded` state — must await it.
    func reparseCurrent() async {
        guard let d = currentDate else { return }
        await reparse(url: url(for: d))
    }

    private func reparse(url: URL) async {
        guard let date = currentDate else { return }
        guard FileManager.default.fileExists(atPath: url.path) else {
            publish(.missing(date: date, expectedURL: url))
            return
        }

        generation &+= 1
        let mine = generation
        // Read the byline here, on the main actor, rather than inside the
        // parser: the parse runs off-actor and `UserDefaults` hands back
        // Cocoa-backed strings.
        let author = UserDefaults.standard.string(forKey: "authorName") ?? "user"

        // Off the main actor: parsing a real day costs hundreds of
        // milliseconds, and doing it here froze the UI on every load and every
        // checkbox click. `ActionItemsDocument` is `Sendable` and the parser is
        // `nonisolated`, so only value types cross.
        let result: Result<ActionItemsDocument, Error> = await Task.detached(priority: .userInitiated) {
            do {
                let data = try Data(contentsOf: url)
                let text = String(data: data, encoding: .utf8) ?? ""
                return .success(try ActionItemsParser.parse(
                    text: text,
                    sourceURL: url,
                    sourceBytes: data.count,
                    inlineCommentAuthor: author
                ))
            } catch {
                return .failure(error)
            }
        }.value

        // A newer request started while this one was parsing — drop it rather
        // than overwrite fresher state with stale content.
        guard mine == generation else { return }

        switch result {
        case .success(let doc): publish(.loaded(doc))
        case .failure(let error): publish(.failed(error))
        }
    }

    /// Assign `state` only when it actually changed.
    ///
    /// `@Published` fires `objectWillChange` on every assignment, equal or not,
    /// and one write reparses twice — once explicitly for responsiveness, once
    /// from the FSEvent the same write triggers. The second parse yields a
    /// byte-identical document, and republishing it rebuilt the entire view
    /// tree (~475 cards, ~1.8 s) for no change at all.
    private func publish(_ next: State) {
        guard state != next else { return }
        state = next
    }

    private func startWatching() {
        watchTask?.cancel()
        let stream = fileEvents.events(for: directory)
        watchTask = Task { [weak self] in
            var debounce: Task<Void, Never>?
            for await event in stream {
                guard let self else { return }
                guard let date = await MainActor.run(body: { self.currentDate }) else { continue }
                let expected = await MainActor.run(body: { self.url(for: date) })
                guard event.url.lastPathComponent == expected.lastPathComponent else { continue }
                debounce?.cancel()
                debounce = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: 250_000_000)
                    guard let self, !Task.isCancelled else { return }
                    await self.reparse(url: expected)
                }
            }
        }
    }

    func url(for date: Date) -> URL {
        directory.appendingPathComponent("action-items-\(ActionItemsDay.stem(for: date)).md")
    }

    deinit { watchTask?.cancel() }
}
