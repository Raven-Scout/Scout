import CryptoKit
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
        /// discarded rather than mis-decoded. 3: `DetectedError` gained a kind
        /// and a block, and the scanner finds tracebacks, so every log is
        /// re-scanned once (card R3).
        static let version = 3

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
    ///
    /// One file per app build (bundle ID) and per vault. It used to be one fixed
    /// `Caches/Scout/session-parse-cache.json` shared by every build and vault,
    /// so a dev build or a second vault rewrote the installed app's cache on
    /// every launch and each then re-parsed every log. Entries self-validate
    /// (name + size + mtime), so sharing never gave wrong data, only misses.
    ///
    /// It is not counts only: each entry keeps the log's parsed fields,
    /// including up to 200 characters of every error line it matched
    /// (`DetectedError.snippet`). Like the logs themselves, it stays in the
    /// owner's own caches directory and the path never names the vault.
    nonisolated static func defaultParseCacheURL(
        scoutDirectory: URL,
        bundleIdentifier: String? = Bundle.main.bundleIdentifier
    ) -> URL? {
        let fm = FileManager.default
        guard let base = try? fm.url(
            for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        ) else { return nil }
        let url = parseCacheURL(
            cachesDirectory: base, scoutDirectory: scoutDirectory, bundleIdentifier: bundleIdentifier
        )
        try? fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        return url
    }

    /// Pure path rule behind `defaultParseCacheURL`:
    /// `<caches>/Scout/parse-cache/<bundle id>/<vault key>.json`. The vault key
    /// is a hash of the standardized vault path, so a path never becomes a
    /// file name and two spellings of one vault share a cache.
    nonisolated static func parseCacheURL(
        cachesDirectory: URL, scoutDirectory: URL, bundleIdentifier: String?
    ) -> URL {
        let bundle = sanitizedComponent(bundleIdentifier ?? "") ?? "unknown-bundle"
        return cachesDirectory
            .appendingPathComponent("Scout", isDirectory: true)
            .appendingPathComponent("parse-cache", isDirectory: true)
            .appendingPathComponent(bundle, isDirectory: true)
            .appendingPathComponent("\(vaultKey(scoutDirectory)).json")
    }

    /// The bundle ID of the shipping app, the only build allowed to delete the
    /// old shared cache file.
    nonisolated static let shippingBundleIdentifier = "com.scout.Scout"

    /// Delete the old shared `<caches>/Scout/session-parse-cache.json` once
    /// this build keeps its own per-vault cache. Only the shipping app does it,
    /// so a dev build (`com.scout.Scout.dev`) can't delete the file the
    /// installed app still reads before it updates. Only that one file goes;
    /// the `Scout/` folder holds the new `parse-cache/` tree. Returns whether a
    /// file was removed. Safe to call on every launch: a missing file is a no-op.
    @discardableResult
    nonisolated static func removeLegacyParseCache(
        cachesDirectory: URL,
        bundleIdentifier: String?,
        fileManager: FileManager = .default
    ) -> Bool {
        guard bundleIdentifier == shippingBundleIdentifier else { return false }
        let legacy = cachesDirectory
            .appendingPathComponent("Scout", isDirectory: true)
            .appendingPathComponent("session-parse-cache.json")
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: legacy.path, isDirectory: &isDirectory), !isDirectory.boolValue
        else { return false }
        return (try? fileManager.removeItem(at: legacy)) != nil
    }

    /// `removeLegacyParseCache` against the per-user caches directory and this
    /// process's bundle ID. Called once at launch.
    nonisolated static func removeLegacyParseCacheAtLaunch() {
        guard let base = try? FileManager.default.url(
            for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: false
        ) else { return }
        removeLegacyParseCache(cachesDirectory: base, bundleIdentifier: Bundle.main.bundleIdentifier)
    }

    /// First 16 hex digits of the SHA-256 of the vault's standardized path.
    nonisolated static func vaultKey(_ scoutDirectory: URL) -> String {
        let path = scoutDirectory.standardizedFileURL.resolvingSymlinksInPath().path
        let digest = SHA256.hash(data: Data(path.utf8))
        return digest.prefix(8).map { String(format: "%02x", $0) }.joined()
    }

    /// A bundle ID as one safe path component, or nil when nothing usable is left.
    nonisolated private static func sanitizedComponent(_ raw: String) -> String? {
        let allowed = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_")
        let cleaned = String(raw.map { allowed.contains($0) ? $0 : "-" })
            .trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return cleaned.isEmpty ? nil : cleaned
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
