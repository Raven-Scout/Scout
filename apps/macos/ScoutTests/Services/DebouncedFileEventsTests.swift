import Testing
import Foundation
@testable import Scout

/// The debouncer runs on a `ManualClock`, so a window closes only when a test
/// advances the clock — never because a starved runner was slow to ingest a
/// burst (#311). Each test waits until the debouncer has ingested every emitted
/// event and is sleeping on its window, then closes the window, so the flush
/// counts below are exact. The waits are liveness bounds, not timing budgets:
/// they return as soon as their condition holds.
@Suite("DebouncedFileEvents", .serialized)
struct DebouncedFileEventsTests {

    private static let interval: Duration = .milliseconds(100)

    private actor Collector {
        private(set) var events: [FileSystemEvent] = []
        func append(_ e: FileSystemEvent) { events.append(e) }
    }

    /// An event source that counts how many events its consumer has asked for.
    /// `DebouncedFileEvents` asks for the next event only after `ingest` of the
    /// previous one returns, so `pulls == n + 1` means all `n` emitted events
    /// are in the debouncer's window.
    private final class PullCountingFS: FileSystemEventSource, @unchecked Sendable {
        private final class IteratorBox: @unchecked Sendable {
            var iterator: AsyncStream<FileSystemEvent>.Iterator
            init(_ iterator: AsyncStream<FileSystemEvent>.Iterator) { self.iterator = iterator }
        }

        private let lock = NSLock()
        private var continuations: [AsyncStream<FileSystemEvent>.Continuation] = []
        private var pullCount = 0

        var pulls: Int { lock.withLock { pullCount } }

        func events(for url: URL) -> AsyncStream<FileSystemEvent> {
            let (inner, continuation) = AsyncStream.makeStream(of: FileSystemEvent.self)
            lock.withLock { continuations.append(continuation) }
            let box = IteratorBox(inner.makeAsyncIterator())
            return AsyncStream(unfolding: {
                self.lock.withLock { self.pullCount += 1 }
                var iterator = box.iterator
                let event = await iterator.next()
                box.iterator = iterator
                return event
            })
        }

        func emit(_ e: FileSystemEvent) {
            lock.withLock { continuations }.forEach { $0.yield(e) }
        }
    }

    private static func consume(
        _ stream: AsyncStream<FileSystemEvent>,
        into collector: Collector
    ) -> Task<Void, Never> {
        Task {
            for await e in stream { await collector.append(e) }
        }
    }

    /// Poll until `condition` holds or `timeout` of wall time passes; returns
    /// whether it held. Liveness only: the clock is frozen until a test
    /// advances it, so waiting longer cannot change what is delivered — it
    /// only lets a starved runner get there. A runner at 8x CPU
    /// oversubscription needed several seconds for the 10 pump hops.
    private static func waitUntil(
        timeout: Duration = .seconds(30),
        _ condition: @Sendable () async -> Bool
    ) async throws -> Bool {
        let deadline = ContinuousClock.now + timeout
        while ContinuousClock.now < deadline {
            if await condition() { return true }
            try await Task.sleep(for: .milliseconds(10))
        }
        return await condition()
    }

    /// Waits until the debouncer has ingested `emitted` events and is sleeping
    /// on its window, then closes the window.
    private static func closeWindow(
        after emitted: Int,
        base: PullCountingFS,
        clock: ManualClock
    ) async throws {
        let armed = try await waitUntil { base.pulls == emitted + 1 && clock.sleeperCount == 1 }
        try #require(armed, "debouncer never ingested \(emitted) events (pulls: \(base.pulls), sleepers: \(clock.sleeperCount))")
        clock.advance(by: interval)
    }

    /// Waits until `count` events are delivered, then returns everything delivered.
    private static func delivered(_ count: Int, to collector: Collector) async throws -> [FileSystemEvent] {
        _ = try await waitUntil { await collector.events.count >= count }
        return await collector.events
    }

    @Test func coalescesBurstIntoOneTrailingEvent() async throws {
        let base = PullCountingFS()
        let clock = ManualClock()
        let debounced = DebouncedFileEvents(base: base, interval: Self.interval, clock: clock)
        let dir = URL(fileURLWithPath: "/watched")
        let file = dir.appendingPathComponent("scout-2026-04-20_08-03.log")

        let collector = Collector()
        let consumer = Self.consume(debounced.events(for: dir), into: collector)
        defer { consumer.cancel() }

        for _ in 0..<10 {
            base.emit(FileSystemEvent(url: file, kind: .modified))
        }
        try await Self.closeWindow(after: 10, base: base, clock: clock)

        let received = try await Self.delivered(1, to: collector)
        #expect(received.count == 1, "a 10-event burst in one window should deliver once, got \(received.count)")
        #expect(received.allSatisfy { $0.url == file })
    }

    @Test func deliversEveryDistinctPathWithoutLoss() async throws {
        let base = PullCountingFS()
        let clock = ManualClock()
        let debounced = DebouncedFileEvents(base: base, interval: Self.interval, clock: clock)
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
        try await Self.closeWindow(after: 10, base: base, clock: clock)

        // Both paths surface (no loss), each exactly once (coalesced).
        let received = try await Self.delivered(2, to: collector)
        #expect(Set(received.map(\.url)) == [fileA, fileB])
        #expect(received.count == 2, "one window should deliver each path once, got \(received.count)")
    }

    @Test func eventsAfterQuietPeriodStillDelivered() async throws {
        let base = PullCountingFS()
        let clock = ManualClock()
        let debounced = DebouncedFileEvents(base: base, interval: Self.interval, clock: clock)
        let dir = URL(fileURLWithPath: "/watched")
        let file = dir.appendingPathComponent("scout-2026-04-20_08-03.log")

        let collector = Collector()
        let consumer = Self.consume(debounced.events(for: dir), into: collector)
        defer { consumer.cancel() }

        for _ in 0..<5 { base.emit(FileSystemEvent(url: file, kind: .modified)) }
        try await Self.closeWindow(after: 5, base: base, clock: clock)
        let afterFirst = try await Self.delivered(1, to: collector)
        #expect(afterFirst.count == 1, "first burst should deliver once, got \(afterFirst.count)")

        // The debouncer must not be one-shot: a burst after the first window
        // closed opens a new window that delivers too.
        for _ in 0..<5 { base.emit(FileSystemEvent(url: file, kind: .modified)) }
        try await Self.closeWindow(after: 10, base: base, clock: clock)
        let afterSecond = try await Self.delivered(2, to: collector)
        #expect(afterSecond.count == 2, "each burst should deliver once, got \(afterSecond.count)")
    }
}
