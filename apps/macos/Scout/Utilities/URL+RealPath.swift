import Foundation

extension URL {
    /// The path with every symlink resolved, as FSEvents reports it. Watching
    /// an unresolved path (`/var/...` rather than `/private/var/...`, or a
    /// vault reached through a symlink) delivers no events. Returns `self`
    /// when the path does not exist.
    ///
    /// `realpath(3)`, not `resolvingSymlinksInPath()`: Foundation strips a
    /// leading `/private`, which is exactly the component FSEvents needs.
    nonisolated func resolvingRealPath() -> URL {
        guard let resolved = realpath(path, nil) else { return self }
        defer { free(resolved) }
        return URL(fileURLWithPath: String(cString: resolved), isDirectory: hasDirectoryPath)
    }
}
