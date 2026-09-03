import Foundation
import Testing
@testable import Scout

/// A per-file event source the test drives by hand.
private final class ManualPerFileEvents: FileSystemEventSource, @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: AsyncStream<FileSystemEvent>.Continuation?

    nonisolated func events(for url: URL) -> AsyncStream<FileSystemEvent> {
        AsyncStream { cont in
            lock.lock(); continuation = cont; lock.unlock()
        }
    }

    func emit(_ event: FileSystemEvent) {
        lock.lock(); let c = continuation; lock.unlock()
        c?.yield(event)
    }
}

/// `State` drives the Action Items view's placeholder/error chrome, and it
/// hand-rolls `==` because `.failed` wraps a non-Equatable `Error`. These pin
/// that comparison down.
@Suite("ActionItemsDocumentService.State equality")
struct ActionItemsDocumentServiceStateTests {

    private let day = Date(timeIntervalSince1970: 1_781_600_000)
    private let other = Date(timeIntervalSince1970: 1_781_700_000)

    private func document(title: String = "Tuesday") -> ActionItemsDocument {
        ActionItemsDocument(
            date: day, title: title, preamble: [], sections: [],
            sourceURL: URL(fileURLWithPath: "/tmp/2026-06-15.md"), sourceBytes: 10)
    }

    private typealias State = ActionItemsDocumentService.State

    @Test("idle equals idle")
    func idle_equality() {
        #expect(State.idle == State.idle)
    }

    @Test("loading compares on its date")
    func loading_comparesDate() {
        #expect(State.loading(day) == State.loading(day))
        #expect(State.loading(day) != State.loading(other))
    }

    @Test("loaded compares on its document")
    func loaded_comparesDocument() {
        #expect(State.loaded(document()) == State.loaded(document()))
        #expect(State.loaded(document()) != State.loaded(document(title: "Wednesday")))
    }

    @Test("missing compares on both date and expected URL")
    func missing_comparesDateAndURL() {
        let a = URL(fileURLWithPath: "/tmp/a.md")
        let b = URL(fileURLWithPath: "/tmp/b.md")
        #expect(State.missing(date: day, expectedURL: a)
                == State.missing(date: day, expectedURL: a))
        #expect(State.missing(date: day, expectedURL: a)
                != State.missing(date: other, expectedURL: a))
        #expect(State.missing(date: day, expectedURL: a)
                != State.missing(date: day, expectedURL: b))
    }

    @Test("all failures compare equal — the wrapped error isn't Equatable")
    func failed_ignoresUnderlyingError() {
        struct A: Error {}
        struct B: Error {}
        #expect(State.failed(A()) == State.failed(B()))
    }

    @Test("different cases never compare equal")
    func acrossCases_neverEqual() {
        struct A: Error {}
        let states: [State] = [
            .idle,
            .loading(day),
            .loaded(document()),
            .missing(date: day, expectedURL: URL(fileURLWithPath: "/tmp/a.md")),
            .failed(A()),
        ]
        for (i, lhs) in states.enumerated() {
            for (j, rhs) in states.enumerated() where i != j {
                #expect(lhs != rhs, "case \(i) should not equal case \(j)")
            }
        }
    }
}

/// The Wishlist / Research tabs reload themselves off FSEvents; these cover
/// the watch path and the state transitions the list chrome reads.
@MainActor
@Suite("PerFileDocumentService watching")
struct PerFileDocumentServiceWatchTests {

    private func makeDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("perfile-watch-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    private func writeItem(
        in dir: URL, file: String, title: String, status: String, priority: String = "medium"
    ) throws {
        try """
        ---
        title: \(title)
        status: \(status)
        priority: \(priority)
        date: 2026-06-15
        ---

        # \(title)
        body
        """.write(to: dir.appendingPathComponent(file), atomically: true, encoding: .utf8)
    }

    @Test("a markdown event triggers a debounced reparse")
    func watching_markdownEventReparses() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let events = ManualPerFileEvents()
        let svc = PerFileDocumentService(directoryURL: dir, fileEvents: events)
        svc.load()
        #expect(svc.items.isEmpty)

        try writeItem(in: dir, file: "2026-06-15-a.md", title: "A", status: "open")
        events.emit(FileSystemEvent(
            url: dir.appendingPathComponent("2026-06-15-a.md"), kind: .created))

        let deadline = Date().addingTimeInterval(3)
        while Date() < deadline, svc.items.isEmpty {
            try await Task.sleep(nanoseconds: 25_000_000)
        }
        #expect(svc.items.count == 1)
        #expect(svc.activeCount == 1)
    }

    @Test("a non-markdown event is ignored")
    func watching_ignoresNonMarkdownEvents() async throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let events = ManualPerFileEvents()
        let svc = PerFileDocumentService(directoryURL: dir, fileEvents: events)
        svc.load()

        try writeItem(in: dir, file: "2026-06-15-a.md", title: "A", status: "open")
        events.emit(FileSystemEvent(url: dir.appendingPathComponent("x.txt"), kind: .modified))

        try await Task.sleep(nanoseconds: 500_000_000)
        #expect(svc.items.isEmpty)
    }

    @Test("items are ordered newest filename first")
    func load_sortsNewestFirst() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try writeItem(in: dir, file: "2026-06-10-old.md", title: "Old", status: "open")
        try writeItem(in: dir, file: "2026-06-15-new.md", title: "New", status: "done")

        let svc = PerFileDocumentService(directoryURL: dir, fileEvents: ManualPerFileEvents())
        svc.load()
        #expect(svc.items.map(\.title) == ["New", "Old"])
        #expect(svc.activeCount == 1)
    }

    @Test("state starts idle and reaches loaded")
    func state_transitions() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let svc = PerFileDocumentService(directoryURL: dir, fileEvents: ManualPerFileEvents())
        #expect(svc.state == .idle)
        svc.load()
        #expect(svc.state == .loaded)
    }

    @Test("reload picks up a status flipped on disk")
    func reload_seesStatusChange() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try writeItem(in: dir, file: "2026-06-15-a.md", title: "A", status: "open")
        let svc = PerFileDocumentService(directoryURL: dir, fileEvents: ManualPerFileEvents())
        svc.load()
        #expect(svc.activeCount == 1)

        try writeItem(in: dir, file: "2026-06-15-a.md", title: "A", status: "done")
        svc.reload()
        #expect(svc.activeCount == 0)
        #expect(svc.items.first?.status == .done)
    }
}
