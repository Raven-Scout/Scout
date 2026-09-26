import Foundation

struct FileSystemEvent: Equatable, Sendable {
    enum Kind: Sendable { case created, modified, deleted, renamed }
    let url: URL
    let kind: Kind
}

protocol FileSystemEventSource: Sendable {
    /// Emits events for the given URL and its descendants.
    /// The stream ends only when the source is deallocated.
    func events(for url: URL) -> AsyncStream<FileSystemEvent>
}
