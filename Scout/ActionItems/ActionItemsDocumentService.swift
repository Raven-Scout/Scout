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

    init(directory: URL, fileEvents: any FileSystemEventSource) {
        self.directory = directory
        self.fileEvents = fileEvents
    }

    /// Load the action-items file for ``date`` (ET-local). Starts (or restarts)
    /// the FSEvents subscription filtered to that date's filename.
    func load(date: Date) async throws {
        currentDate = date
        state = .loading(date)
        let fileURL = url(for: date)
        do {
            try reparse(url: fileURL)
        } catch {
            state = .failed(error)
        }
        startWatching()
    }

    /// Recompute the displayed document's URL for the currently-loaded date.
    /// Called by the writer after a successful CLI invocation so the user
    /// sees the change ASAP even if FSEvents is briefly laggy.
    func reparseCurrent() {
        guard let d = currentDate else { return }
        do {
            try reparse(url: url(for: d))
        } catch {
            state = .failed(error)
        }
    }

    private func reparse(url: URL) throws {
        guard FileManager.default.fileExists(atPath: url.path),
              let date = currentDate else {
            if let d = currentDate { state = .missing(date: d, expectedURL: url) }
            return
        }
        let data = try Data(contentsOf: url)
        let text = String(data: data, encoding: .utf8) ?? ""
        let doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: data.count)
        _ = date
        state = .loaded(doc)
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
                    guard let self else { return }
                    await MainActor.run {
                        do {
                            try self.reparse(url: expected)
                        } catch {
                            self.state = .failed(error)
                        }
                    }
                }
            }
        }
    }

    func url(for date: Date) -> URL {
        directory.appendingPathComponent("action-items-\(ActionItemsDay.stem(for: date)).md")
    }

    deinit { watchTask?.cancel() }
}
