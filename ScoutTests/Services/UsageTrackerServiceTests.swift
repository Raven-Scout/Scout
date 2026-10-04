import Testing
import Foundation
@testable import Scout

@Suite("UsageTrackerService")
struct UsageTrackerServiceTests {
    @Test func parsesSessionLinesAndFiltersRunnerDuplicates() async throws {
        let fixture = Self.fixtureURL.appendingPathComponent("usage-tracker.jsonl")
        let service = await UsageTrackerService(trackerURL: fixture, fileEvents: NoopFS())

        let entries = try await service.loadInitial()
        #expect(!entries.isEmpty, "fixture should have at least one session entry")
        #expect(entries.allSatisfy { ($0.source ?? "session") == "session" })
    }

    @Test func costLookupByTypeAndTimestamp() async throws {
        let json = """
        {"ts":"2026-04-19T12:03:00Z","ts_et":"2026-04-19 08:03 EDT","type":"briefing","budget_cap":10,"budget_spent":4.12,"exit_code":0,"source":"session"}
        {"ts":"2026-04-19T12:03:00Z","ts_et":"2026-04-19 08:03 EDT","type":"briefing","budget_cap":10,"budget_spent":0,"exit_code":0,"source":"runner"}
        """
        let tmp = try Self.writeTemp(json)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let service = await UsageTrackerService(trackerURL: tmp, fileEvents: NoopFS())
        _ = try await service.loadInitial()

        let target = ISO8601DateFormatter().date(from: "2026-04-19T12:03:00Z")!
        let match = await service.cost(matching: "briefing", near: target, tolerance: 120)
        #expect(match?.budgetSpent == Decimal(string: "4.12"))
        #expect(match?.source == "session")
    }

    // MARK: - tolerance of real log files

    @Test("one invalid UTF-8 byte does not discard every entry in the file")
    func corruptByteDoesNotDiscardWholeFile() async throws {
        // `usage-tracker.jsonl` is appended by a shell script while runs are
        // in flight; a torn write can leave a byte that is not valid UTF-8.
        // Decoding the whole file as a String first turned that into total
        // data loss — every entry vanished from the Usage card, silently.
        var bytes = Data("""
        {"ts":"2026-04-19T12:03:00Z","ts_et":"2026-04-19 08:03 EDT","type":"briefing","budget_cap":10,"budget_spent":4.12,"exit_code":0,"source":"session"}

        """.utf8)
        bytes.append(0xFF)                      // never valid in UTF-8
        bytes.append(contentsOf: Data("\n".utf8))
        bytes.append(contentsOf: Data("""
        {"ts":"2026-04-19T13:03:00Z","ts_et":"2026-04-19 09:03 EDT","type":"dreaming","budget_cap":10,"budget_spent":1.50,"exit_code":0,"source":"session"}

        """.utf8))

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".jsonl")
        try bytes.write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        let service = await UsageTrackerService(trackerURL: url, fileEvents: NoopFS())
        let entries = try await service.loadInitial()

        #expect(entries.count == 2, "both intact lines must survive one corrupt byte")
        #expect(entries.map(\.type).sorted() == ["briefing", "dreaming"])
    }

    @Test("timestamps parse with and without fractional seconds")
    func parsesBothTimestampShapes() async throws {
        // Pins the behaviour a shared, hoisted formatter must preserve: the
        // two shapes need two differently-configured formatters, and neither
        // may be mutated in place once shared.
        let json = """
        {"ts":"2026-04-19T12:03:00Z","ts_et":"2026-04-19 08:03 EDT","type":"briefing","budget_cap":10,"budget_spent":1,"exit_code":0,"source":"session"}
        {"ts":"2026-04-19T13:03:00.123Z","ts_et":"2026-04-19 09:03 EDT","type":"dreaming","budget_cap":10,"budget_spent":2,"exit_code":0,"source":"session"}
        """
        let tmp = try Self.writeTemp(json)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let service = await UsageTrackerService(trackerURL: tmp, fileEvents: NoopFS())
        let entries = try await service.loadInitial()

        #expect(entries.count == 2, "both timestamp shapes must decode")
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        #expect(entries.first { $0.type == "briefing" }?.ts
                == plain.date(from: "2026-04-19T12:03:00Z"))
        #expect(entries.first { $0.type == "dreaming" }?.ts
                == fractional.date(from: "2026-04-19T13:03:00.123Z"))
    }

    // MARK: - watching

    @Test("an event emitted right after loadInitial() returns refreshes entries")
    @MainActor func eventRightAfterLoadInitialRefreshes() async throws {
        let tmp = try Self.writeTemp("""
        {"ts":"2026-04-19T12:03:00Z","ts_et":"2026-04-19 08:03 EDT","type":"briefing","budget_cap":10,"budget_spent":1,"exit_code":0,"source":"session"}
        """)
        defer { try? FileManager.default.removeItem(at: tmp) }

        let fakeFS = InjectableFS()
        let service = UsageTrackerService(trackerURL: tmp, fileEvents: fakeFS)
        let initial = try await service.loadInitial()
        #expect(initial.count == 1)

        // No suspension between loadInitial() returning and the emit: on the
        // main actor the watch task has not run yet, so the event lands only
        // if startWatching() subscribed synchronously.
        try """
        {"ts":"2026-04-19T12:03:00Z","ts_et":"2026-04-19 08:03 EDT","type":"briefing","budget_cap":10,"budget_spent":1,"exit_code":0,"source":"session"}
        {"ts":"2026-04-19T13:03:00Z","ts_et":"2026-04-19 09:03 EDT","type":"dreaming","budget_cap":10,"budget_spent":2,"exit_code":0,"source":"session"}
        """.write(to: tmp, atomically: true, encoding: .utf8)
        fakeFS.emit(FileSystemEvent(url: tmp, kind: .modified))

        await waitUntil("entries never refreshed after the event") { service.entries.count == 2 }
    }

    // MARK: - helpers

    static var fixtureURL: URL {
        Bundle(for: FixtureAnchor.self).resourceURL!
    }

    static func writeTemp(_ s: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".jsonl")
        try s.write(to: url, atomically: true, encoding: .utf8)
        return url
    }
}

struct NoopFS: FileSystemEventSource {
    func events(for url: URL) -> AsyncStream<FileSystemEvent> {
        AsyncStream { $0.finish() }
    }
}
