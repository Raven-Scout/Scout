import Combine
import Testing
import Foundation
@testable import Scout

/// Reparse cost and ordering.
///
/// A single checkbox click used to cost two full parses of a 1.8 MB file and
/// two whole-tree SwiftUI rebuilds, all on the main actor: `handleOp` calls
/// `reparseCurrent()` for responsiveness, scoutctl's own write independently
/// trips FSEvents, and `@Published` republished the byte-identical second
/// document because nothing compared it to the current one.
@Suite("ActionItemsDocumentService — reparse")
@MainActor
struct DocumentServiceReparseTests {

    static func tmpDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    static func day(_ y: Int, _ m: Int, _ d: Int) -> Date {
        Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone.current, year: y, month: m, day: d
        ))!
    }

    /// A file with `taskCount` tasks, so a parse is slow enough to observe.
    static func markdown(taskCount: Int, marker: String = "A") -> String {
        var s = "# Action Items — synthetic\n\n## 🔴 Urgent\n\n"
        for i in 0 ..< taskCount {
            s += "- [ ] [#TAG\(i % 90)] **\(marker) task \(i) [[people/priya]]** — body text for \(i) with PROJ-\(i) and `code`\n"
            s += "  - detail sub-bullet for task \(i)\n"
        }
        return s
    }

    // MARK: - The equality gate

    @Test("Reparsing an unchanged file does not republish")
    func unchangedReparseDoesNotRepublish() async throws {
        let dir = try Self.tmpDir()
        let date = Self.day(2026, 4, 20)
        let url = dir.appendingPathComponent("action-items-2026-04-20.md")
        try Self.markdown(taskCount: 30).write(to: url, atomically: true, encoding: .utf8)

        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())
        try await service.load(date: date)

        var publishes = 0
        let token = service.objectWillChange.sink { _ in publishes += 1 }
        defer { token.cancel() }

        // The FSEvent-driven reparse that follows every write lands here: same
        // bytes, same document. It must not drive a whole-tree rebuild.
        await service.reparseCurrent()

        #expect(publishes == 0, "unchanged reparse published \(publishes) time(s)")
    }

    @Test("Reparsing a changed file does republish")
    func changedReparseRepublishes() async throws {
        let dir = try Self.tmpDir()
        let date = Self.day(2026, 4, 20)
        let url = dir.appendingPathComponent("action-items-2026-04-20.md")
        try Self.markdown(taskCount: 30).write(to: url, atomically: true, encoding: .utf8)

        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())
        try await service.load(date: date)

        var publishes = 0
        let token = service.objectWillChange.sink { _ in publishes += 1 }
        defer { token.cancel() }

        try Self.markdown(taskCount: 31).write(to: url, atomically: true, encoding: .utf8)
        await service.reparseCurrent()

        #expect(publishes >= 1, "changed reparse did not publish")
        guard case .loaded(let doc) = service.state else {
            Issue.record("expected .loaded, got \(service.state)"); return
        }
        #expect(doc.sections.first?.tasks.count == 31)
    }

    // MARK: - Ordering

    @Test("A slow earlier load never overwrites a newer one")
    func slowLoadDoesNotClobberNewer() async throws {
        // Switching days quickly: the first day's file is big and slow to
        // parse, the second is tiny. Once parsing moves off the main actor the
        // two overlap, and without a generation guard the stale result lands
        // last and the user is left looking at the wrong day.
        let dir = try Self.tmpDir()
        let slowDate = Self.day(2026, 4, 20)
        let fastDate = Self.day(2026, 4, 21)
        try Self.markdown(taskCount: 4000, marker: "SLOW")
            .write(to: dir.appendingPathComponent("action-items-2026-04-20.md"),
                   atomically: true, encoding: .utf8)
        try Self.markdown(taskCount: 1, marker: "FAST")
            .write(to: dir.appendingPathComponent("action-items-2026-04-21.md"),
                   atomically: true, encoding: .utf8)

        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())

        async let slow: Void = service.load(date: slowDate)
        // Let the slow parse get in flight, then supersede it.
        try await Task.sleep(nanoseconds: 20_000_000)
        try await service.load(date: fastDate)
        // `load` returns only after its own parse has passed the generation
        // guard, so once both loads have returned nothing is left in flight.
        _ = try? await slow

        guard case .loaded(let doc) = service.state else {
            Issue.record("expected .loaded, got \(service.state)"); return
        }
        #expect(doc.sections.first?.tasks.first?.plainSubject.contains("FAST") == true,
                "stale slow parse clobbered the newer load")
    }

    // MARK: - Main-actor blocking

    @Test("Parsing does not block the main actor")
    func loadDoesNotBlockTheMainActor() async throws {
        let dir = try Self.tmpDir()
        let date = Self.day(2026, 4, 20)
        try Self.markdown(taskCount: 4000)
            .write(to: dir.appendingPathComponent("action-items-2026-04-20.md"),
                   atomically: true, encoding: .utf8)

        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())

        // Queued before the load starts. While the parse ran synchronously on
        // the main actor there was no suspension point for this to slip
        // through, so it could only run after the load had finished.
        var order: [String] = []
        let other = Task { @MainActor in order.append("other") }

        try await service.load(date: date)
        order.append("load")
        await other.value

        #expect(order.first == "other",
                "main actor was blocked through the parse (order: \(order))")
    }

    // MARK: - Review fixes

    @Test("A slow earlier load never overwrites a newer missing-day state")
    func slowLoadDoesNotClobberMissingDay() async throws {
        // Same race as `slowLoadDoesNotClobberNewer`, but the superseding day
        // has no file yet (today before the briefing, or tomorrow). `.missing`
        // must supersede the in-flight parse too, or the previous day's tasks
        // land under the new dateline with the missing-file affordance gone.
        let dir = try Self.tmpDir()
        let slowDate = Self.day(2026, 4, 20)
        let missingDate = Self.day(2026, 4, 21)
        try Self.markdown(taskCount: 4000, marker: "SLOW")
            .write(to: dir.appendingPathComponent("action-items-2026-04-20.md"),
                   atomically: true, encoding: .utf8)

        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())

        async let slow: Void = service.load(date: slowDate)
        try await Task.sleep(nanoseconds: 20_000_000)
        try await service.load(date: missingDate)
        _ = try? await slow

        guard case .missing(let date, _) = service.state else {
            Issue.record("expected .missing, got \(service.state)"); return
        }
        #expect(date == missingDate, "stale slow parse clobbered the missing-day state")
    }

    @Test("A failed reparse always republishes, even after a prior failure")
    func failuresAlwaysRepublish() async throws {
        // `State ==` treats any two failures as equal, so without a bypass in
        // `publish` a second error would be swallowed and the first error's
        // text would stay on screen.
        let dir = try Self.tmpDir()
        let date = Self.day(2026, 4, 20)
        // A directory where the file should be: the read fails.
        try FileManager.default.createDirectory(
            at: dir.appendingPathComponent("action-items-2026-04-20.md"),
            withIntermediateDirectories: true
        )

        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())
        try await service.load(date: date)
        guard case .failed = service.state else {
            Issue.record("expected .failed, got \(service.state)"); return
        }

        var publishes = 0
        let token = service.objectWillChange.sink { _ in publishes += 1 }
        defer { token.cancel() }

        await service.reparseCurrent()
        #expect(publishes == 1, "a repeated failure was swallowed by the equality gate")
    }

    @Test("Reloading the day already on screen does not flash `.loading`")
    func reloadOfSameDayKeepsDocumentOnScreen() async throws {
        // Returning to the tab recreates the view and calls `load` for the day
        // the service already holds. Publishing `.loading` there would tear
        // the card tree down to a spinner and rebuild it for an identical
        // document.
        let dir = try Self.tmpDir()
        let date = Self.day(2026, 4, 20)
        try Self.markdown(taskCount: 5)
            .write(to: dir.appendingPathComponent("action-items-2026-04-20.md"),
                   atomically: true, encoding: .utf8)

        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())
        try await service.load(date: date)

        var publishes = 0
        var sawLoading = false
        let willChange = service.objectWillChange.sink { _ in publishes += 1 }
        let values = service.$state.dropFirst().sink { next in
            if case .loading = next { sawLoading = true }
        }
        defer { willChange.cancel(); values.cancel() }

        try await service.load(date: date)

        #expect(!sawLoading, "same-day reload published .loading")
        #expect(publishes == 0, "same-day reload republished an identical document")
        guard case .loaded = service.state else {
            Issue.record("expected .loaded, got \(service.state)"); return
        }
    }
}
