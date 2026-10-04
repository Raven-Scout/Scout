import Foundation
import CoreServices
import OSLog

/// FSEvents-based implementation of `FileSystemEventSource`.
/// Watches a directory (and its descendants) and emits events for file changes.
final class FileWatcher: FileSystemEventSource, @unchecked Sendable {
    private static let log = Logger(subsystem: "com.scout.Scout", category: "FileWatcher")

    private let startStream: @Sendable (FSEventStreamRef) -> Bool

    /// `startStream` is a test seam: `FSEventStreamStart` only fails in
    /// conditions a test cannot set up through this type.
    nonisolated init(startStream: @escaping @Sendable (FSEventStreamRef) -> Bool = { FSEventStreamStart($0) }) {
        self.startStream = startStream
    }

    func events(for url: URL) -> AsyncStream<FileSystemEvent> {
        AsyncStream { continuation in
            // Pass the box through context.info BEFORE FSEventStreamCreate copies it.
            let box = ContinuationBox(continuation: continuation)
            let boxPtr = Unmanaged.passRetained(box).toOpaque()
            var context = FSEventStreamContext(
                version: 0,
                info: boxPtr,
                retain: nil,
                release: nil,
                copyDescription: nil
            )

            let pathsToWatch = [url.path] as CFArray
            let streamRef = FSEventStreamCreate(
                nil,
                { _, info, numEvents, eventPaths, eventFlags, _ in
                    guard let info else { return }
                    let continuation = Unmanaged<ContinuationBox>
                        .fromOpaque(info).takeUnretainedValue().continuation
                    let paths = Unmanaged<CFArray>.fromOpaque(eventPaths)
                        .takeUnretainedValue() as! [String]
                    let flags = UnsafeBufferPointer<FSEventStreamEventFlags>(
                        start: eventFlags, count: numEvents
                    )
                    for i in 0..<numEvents {
                        let kind: FileSystemEvent.Kind
                        let f = flags[i]
                        if f & UInt32(kFSEventStreamEventFlagItemCreated) != 0 { kind = .created }
                        else if f & UInt32(kFSEventStreamEventFlagItemRemoved) != 0 { kind = .deleted }
                        else if f & UInt32(kFSEventStreamEventFlagItemRenamed) != 0 { kind = .renamed }
                        else { kind = .modified }
                        continuation.yield(FileSystemEvent(
                            url: URL(fileURLWithPath: paths[i]),
                            kind: kind
                        ))
                    }
                },
                &context,
                pathsToWatch,
                UInt64(kFSEventStreamEventIdSinceNow),
                0.1,
                UInt32(kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagUseCFTypes)
            )

            guard let stream = streamRef else {
                Unmanaged<ContinuationBox>.fromOpaque(boxPtr).release()
                continuation.finish()
                return
            }

            FSEventStreamSetDispatchQueue(stream, DispatchQueue(label: "scout.filewatcher"))

            let tearDown: @Sendable () -> Void = {
                FSEventStreamStop(stream)
                FSEventStreamInvalidate(stream)
                FSEventStreamRelease(stream)
                Unmanaged<ContinuationBox>.fromOpaque(boxPtr).release()
            }

            // A stream that never starts never yields; finish it so consumers'
            // `for await` loops end instead of hanging. `onTermination` is not
            // installed yet, so `finish()` here cannot release the box twice.
            guard startStream(stream) else {
                Self.log.error("FSEventStreamStart failed for \(url.path, privacy: .public)")
                tearDown()
                continuation.finish()
                return
            }

            continuation.onTermination = { _ in tearDown() }
        }
    }
}

private final class ContinuationBox: @unchecked Sendable {
    let continuation: AsyncStream<FileSystemEvent>.Continuation
    init(continuation: AsyncStream<FileSystemEvent>.Continuation) {
        self.continuation = continuation
    }
}
