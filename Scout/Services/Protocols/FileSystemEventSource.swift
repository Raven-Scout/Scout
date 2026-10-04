import Foundation

struct FileSystemEvent: Equatable, Sendable {
    enum Kind: Sendable { case created, modified, deleted, renamed }
    let url: URL
    let kind: Kind
}

protocol FileSystemEventSource: Sendable {
    /// Emits events for the given URL and its descendants.
    /// The stream runs until the consumer stops iterating; it finishes right
    /// away if the source cannot watch `url` at all.
    func events(for url: URL) -> AsyncStream<FileSystemEvent>
}
