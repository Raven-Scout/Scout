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
        _ = try? await slow

        // Give any stale in-flight parse every chance to land late.
        try await Task.sleep(nanoseconds: 1_500_000_000)

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
}
