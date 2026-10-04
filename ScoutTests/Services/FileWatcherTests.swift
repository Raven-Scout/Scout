import Testing
import Foundation
@testable import Scout

@Suite("FileWatcher")
struct FileWatcherTests {
    @Test func emitsEventOnFileCreation() async throws {
        let tmp = try FileManager.default.url(
            for: .itemReplacementDirectory,
            in: .userDomainMask,
            appropriateFor: FileManager.default.temporaryDirectory,
            create: true
        )
        defer { try? FileManager.default.removeItem(at: tmp) }

        let watcher = FileWatcher()
        let stream = watcher.events(for: tmp)

        // Give FSEvents a moment to arm before we touch files
        try await Task.sleep(nanoseconds: 300_000_000)

        // The stream buffers, so the write needn't wait for a consumer — and
        // doing it here means a failed write fails the test, not a timeout.
        let file = tmp.appendingPathComponent("hello.txt")
        try "hi".write(to: file, atomically: true, encoding: .utf8)

        // Wait for *this file's* event, not just any. The first event on the
        // stream is usually the watched directory's own creation — made before
        // the stream started, reported late — so "any event" passed without
        // the write ever being seen.
        //
        // And assert liveness — the event arrives — not latency. It hops from
        // `fseventsd` to the watcher's dispatch queue to this task on the
        // cooperative pool, and while the whole suite runs in parallel any hop
        // can stall for seconds: a 3 s ceiling here flaked under that load
        // while passing alone. Same reasoning as `waitUntil`'s budget.
        let collected = await Self.firstEvent(
            named: file.lastPathComponent, from: stream, within: .seconds(30)
        )

        #expect(collected != nil, "expected an FS event for the file just created")
    }

    /// The first event `stream` yields for a file called `name`, or `nil` if
    /// none arrives within `budget`.
    private static func firstEvent(
        named name: String,
        from stream: AsyncStream<FileSystemEvent>,
        within budget: Duration
    ) async -> FileSystemEvent? {
        await withTaskGroup(of: FileSystemEvent?.self) { group in
            group.addTask {
                for await event in stream where event.url.lastPathComponent == name {
                    return event
                }
                return nil
            }
            group.addTask {
                try? await Task.sleep(for: budget)
                return nil
            }
            defer { group.cancelAll() }
            return await group.next() ?? nil
        }
    }

    @Test("a stream that fails to start finishes instead of hanging")
    @MainActor func failedStartFinishesStream() async throws {
        let tmp = try FileManager.default.url(
            for: .itemReplacementDirectory,
            in: .userDomainMask,
            appropriateFor: FileManager.default.temporaryDirectory,
            create: true
        )
        defer { try? FileManager.default.removeItem(at: tmp) }

        // `FSEventStreamStart` returning false used to be ignored: the stream
        // never yielded and never finished, so every `for await` over it hung.
        let watcher = FileWatcher(startStream: { _ in false })
        let stream = watcher.events(for: tmp)

        final class Flag { var finished = false }
        let flag = Flag()
        let consumer = Task { @MainActor in
            for await _ in stream {}
            flag.finished = true
        }
        defer { consumer.cancel() }

        await waitUntil("the consumer's for-await loop never ended") { flag.finished }
    }
}
