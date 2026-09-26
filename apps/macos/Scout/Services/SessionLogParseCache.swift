import Foundation

// Persistent parse cache for session run logs.
//
// `loadInitial` parses every log in the logs directory at launch. Parsing one
// log (file read + five regex scans + timestamp parsing) is cheap individually
// but the directory grows without bound, so a long-lived install ends up
// re-parsing hundreds of immutable logs on every launch — a multi-second CPU
// burst (the "app keeps hanging" report). A completed log never changes, so its
// parsed body can be cached keyed by file identity (size + modification date)
// and reused forever; only new or still-growing ("running") logs are re-parsed.

extension SessionLogService {

    /// On-disk cache of parsed log bodies, keyed by log file name.
    struct ParseCache: Codable, Equatable {
        /// Bump when `Entry`/`ParsedBody` shape changes so stale caches are
        /// discarded rather than mis-decoded.
        static let version = 1

        var version: Int
        var entries: [String: Entry]

        init(version: Int = ParseCache.version, entries: [String: Entry] = [:]) {
            self.version = version
            self.entries = entries
        }

        /// A cached parse plus the file identity it was derived from. If the log
        /// on disk no longer matches `sizeBytes`/`modifiedAt`, the entry is stale.
        struct Entry: Codable, Equatable {
            var sizeBytes: Int64
            var modifiedAt: Date
            var body: ParsedBody
        }
    }

    /// Identity of one log file on disk — enough to key the cache and to parse.
    struct LogFileMeta: Equatable, Sendable {
        let url: URL
        let name: String
        let sizeBytes: Int64
        let modifiedAt: Date
    }

    /// Decide, per log file, whether to reuse a cached parse or re-parse via
    /// `parse`. Pure (no I/O): `parse` is injected so the decision logic is
    /// testable without touching disk. Entries for files no longer present are
    /// dropped from the returned cache, so it can't grow unbounded.
    ///
    /// - Returns: parsed bodies keyed by file name, the rebuilt cache to persist,
    ///   and the names that actually had to be parsed (for diagnostics/tests).
    nonisolated static func resolveCachedBodies(
        files: [LogFileMeta],
        cache: ParseCache,
        parse: (LogFileMeta) -> ParsedBody?
    ) -> (bodies: [String: ParsedBody], updatedCache: ParseCache, parsedNames: [String]) {
        // A schema bump invalidates the whole cache — treat old entries as absent.
        let valid = cache.version == ParseCache.version ? cache.entries : [:]
        var bodies: [String: ParsedBody] = [:]
        var newEntries: [String: ParseCache.Entry] = [:]
        var parsedNames: [String] = []
        for file in files {
            if let hit = valid[file.name],
               hit.sizeBytes == file.sizeBytes,
               hit.modifiedAt == file.modifiedAt {
                // Cache hit: identical file identity → reuse the parsed body.
                bodies[file.name] = hit.body
                newEntries[file.name] = hit
            } else if let parsed = parse(file) {
                // Miss (new file, or size/mtime changed → a still-running log
                // that grew): parse fresh and record the new identity.
                bodies[file.name] = parsed
                newEntries[file.name] = ParseCache.Entry(
                    sizeBytes: file.sizeBytes,
                    modifiedAt: file.modifiedAt,
                    body: parsed
                )
                parsedNames.append(file.name)
            }
            // parse == nil (unparseable) → omit; not cached, retried next launch.
        }
        // `newEntries` only contains files seen this pass, so entries for logs
        // deleted from disk fall out — the cache tracks the directory, no growth.
        return (bodies, ParseCache(entries: newEntries), parsedNames)
    }

    // MARK: - Disk persistence

    /// Default cache location: the per-user caches directory, kept OUT of the
    /// logs directory so writing it never trips that directory's file watcher.
    /// A single fixed file is fine — the cache self-validates per entry (name +
    /// size + mtime), so a stale or shared file degrades to a cache miss, never
    /// to wrong data.
    nonisolated static func defaultParseCacheURL() -> URL? {
        let fm = FileManager.default
        guard let base = try? fm.url(
            for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        ) else { return nil }
        let dir = base.appendingPathComponent("Scout", isDirectory: true)
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("session-parse-cache.json")
    }

    /// Load the cache, or an empty one if absent/unreadable/corrupt (any failure
    /// degrades to a full reparse, which then rewrites a good cache).
    nonisolated static func loadParseCache(at url: URL?) -> ParseCache {
        guard let url,
              let data = try? Data(contentsOf: url),
              let cache = try? JSONDecoder().decode(ParseCache.self, from: data)
        else { return ParseCache() }
        return cache
    }

    nonisolated static func saveParseCache(_ cache: ParseCache, at url: URL?) {
        guard let url, let data = try? JSONEncoder().encode(cache) else { return }
        try? data.write(to: url, options: .atomic)
    }
}
