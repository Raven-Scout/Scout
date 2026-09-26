import Testing
import Foundation
@testable import Scout

/// Timing-based suite: runs serialized and asserts coalescing contracts
/// (far fewer deliveries than emits, no path loss) rather than exact
/// flush counts — under parallel-test CPU load a burst can legitimately
/// straddle two debounce windows.
@Suite("DebouncedFileEvents", .serialized)
struct DebouncedFileEventsTests {

    private actor Collector {
        private(set) var events: [FileSystemEvent] = []
        func append(_ e: FileSystemEvent) { events.append(e) }
    }

    private static func consume(
        _ stream: AsyncStream<FileSystemEvent>,
        into collector: Collector
    ) -> Task<Void, Never> {
        Task {
            for await e in stream { await collector.append(e) }
        }
    }

    /// Poll until `condition` over the collected events holds, up to ~2s.
    private static func waitUntil(
        _ collector: Collector,
        _ condition: @Sendable ([FileSystemEvent]) -> Bool
    ) async throws -> [FileSystemEvent] {
        for _ in 0..<40 {
            let events = await collector.events
            if condition(events) { return events }
            try await Task.sleep(for: .milliseconds(50))
        }
        return await collector.events
    }

    @Test func coalescesBurstIntoFewTrailingEvents() async throws {
        let base = InjectableFS()
        let debounced = DebouncedFileEvents(base: base, interval: .milliseconds(100))
        let dir = URL(fileURLWithPath: "/watched")
        let file = dir.appendingPathComponent("scout-2026-04-20_08-03.log")

        let collector = Collector()
        let consumer = Self.consume(debounced.events(for: dir), into: collector)
        defer { consumer.cancel() }

        for _ in 0..<10 {
            base.emit(FileSystemEvent(url: file, kind: .modified))
        }

        _ = try await Self.waitUntil(collector) { !$0.isEmpty }
        // Settle one extra window so any straggler flush lands before counting.
        try await Task.sleep(for: .milliseconds(250))

        let received = await collector.events
        #expect((1...3).contains(received.count), "10-event burst should coalesce to a few deliveries, got \(received.count)")
        #expect(received.allSatisfy { $0.url == file })
    }

    @Test func deliversEveryDistinctPathWithoutLoss() async throws {
        let base = InjectableFS()
        let debounced = DebouncedFileEvents(base: base, interval: .milliseconds(100))
        let dir = URL(fileURLWithPath: "/watched")
        let fileA = dir.appendingPathComponent("scout-2026-04-20_08-03.log")
        let fileB = dir.appendingPathComponent("dreaming-2026-04-20_22-00.log")

        let collector = Collector()
        let consumer = Self.consume(debounced.events(for: dir), into: collector)
        defer { consumer.cancel() }

        for _ in 0..<5 {
            base.emit(FileSystemEvent(url: fileA, kind: .modified))
            base.emit(FileSystemEvent(url: fileB, kind: .modified))
        }

        let received = try await Self.waitUntil(collector) {
            Set($0.map(\.url)) == [fileA, fileB]
        }
        // Both paths must surface (no loss), but coalesced — not 10 deliveries.
        #expect(Set(received.map(\.url)) == [fileA, fileB])
        #expect(received.count <= 4, "interleaved 10-event burst should coalesce, got \(received.count)")
    }

    @Test func eventsAfterQuietPeriodStillDelivered() async throws {
        let base = InjectableFS()
        let debounced = DebouncedFileEvents(base: base, interval: .milliseconds(100))
        let dir = URL(fileURLWithPath: "/watched")
        let file = dir.appendingPathComponent("scout-2026-04-20_08-03.log")

        let collector = Collector()
        let consumer = Self.consume(debounced.events(for: dir), into: collector)
        defer { consumer.cancel() }

        for _ in 0..<5 { base.emit(FileSystemEvent(url: file, kind: .modified)) }
        let afterFirst = try await Self.waitUntil(collector) { !$0.isEmpty }
        #expect(!afterFirst.isEmpty, "first burst never flushed")

        for _ in 0..<5 { base.emit(FileSystemEvent(url: file, kind: .modified)) }
        let afterSecond = try await Self.waitUntil(collector) { $0.count > afterFirst.count }

        // The debouncer must not be one-shot: a burst after a quiet period
        // still surfaces, and the total stays coalesced.
        #expect(afterSecond.count > afterFirst.count, "second burst never flushed")
        #expect(afterSecond.count <= 4, "bursts should coalesce, got \(afterSecond.count)")
    }
}
