import Foundation
import CoreServices

/// FSEvents-based implementation of `FileSystemEventSource`.
/// Watches a directory (and its descendants) and emits events for file changes.
final class FileWatcher: FileSystemEventSource, @unchecked Sendable {
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
            FSEventStreamStart(stream)

            continuation.onTermination = { _ in
                FSEventStreamStop(stream)
                FSEventStreamInvalidate(stream)
                FSEventStreamRelease(stream)
                Unmanaged<ContinuationBox>.fromOpaque(boxPtr).release()
            }
        }
    }
}

private final class ContinuationBox: @unchecked Sendable {
    let continuation: AsyncStream<FileSystemEvent>.Continuation
    init(continuation: AsyncStream<FileSystemEvent>.Continuation) {
        self.continuation = continuation
    }
}
