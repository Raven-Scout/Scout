import Foundation

/// Wraps a base `FileSystemEventSource`, coalescing rapid per-path event
/// bursts into a single trailing event per path. Scout session logs are
/// appended continuously while a run executes; without coalescing, every
/// FSEvent triggers a full re-parse downstream and the main actor can fall
/// permanently behind under heavy vault churn (issue #22).
struct DebouncedFileEvents: FileSystemEventSource {
    let base: any FileSystemEventSource
    let interval: Duration
    /// Times the coalescing window. Tests inject a manual clock so a window
    /// closes when the test says, not when a starved runner gets to it.
    let clock: any Clock<Duration>

    init(base: any FileSystemEventSource, interval: Duration, clock: any Clock<Duration> = ContinuousClock()) {
        self.base = base
        self.interval = interval
        self.clock = clock
    }

    func events(for url: URL) -> AsyncStream<FileSystemEvent> {
        // Subscribe to the base source synchronously so no event emitted
        // between this call and the pump task starting is dropped.
        let baseStream = base.events(for: url)
        let interval = self.interval
        let clock = self.clock
        return AsyncStream { continuation in
            let coalescer = Coalescer(interval: interval, clock: clock, continuation: continuation)
            let pump = Task {
                for await event in baseStream {
                    await coalescer.ingest(event)
                }
                await coalescer.finish()
            }
            continuation.onTermination = { _ in pump.cancel() }
        }
    }

    /// Collects the last event per path within a window. The window opens on
    /// the first event after a flush and closes `interval` later, emitting one
    /// event per touched path. A fixed window (rather than a timer that resets
    /// on every event) guarantees a steadily-appending file still surfaces an
    /// update once per interval instead of only when the churn stops.
    private actor Coalescer {
        private let interval: Duration
        private let clock: any Clock<Duration>
        private let continuation: AsyncStream<FileSystemEvent>.Continuation
        private var pending: [URL: FileSystemEvent] = [:]
        private var flushTask: Task<Void, Never>?

        init(interval: Duration, clock: any Clock<Duration>, continuation: AsyncStream<FileSystemEvent>.Continuation) {
            self.interval = interval
            self.clock = clock
            self.continuation = continuation
        }

        func ingest(_ event: FileSystemEvent) {
            pending[event.url] = event
            guard flushTask == nil else { return }
            flushTask = Task {
                try? await clock.sleep(for: interval)
                guard !Task.isCancelled else { return }
                flush()
            }
        }

        private func flush() {
            flushTask = nil
            for event in pending.values { continuation.yield(event) }
            pending.removeAll()
        }

        func finish() {
            flushTask?.cancel()
            flush()
            continuation.finish()
        }
    }
}
