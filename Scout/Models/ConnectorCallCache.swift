import Foundation

/// Per-file memo for parsed `connector-calls-*.jsonl` rows.
///
/// Connector health reloads on every FSEvent in `.scout-logs`, and a live
/// Scout run appends to today's file continuously. Without memoisation each
/// event re-parsed the entire retained window — ~27k rows, ~0.9s of work —
/// even though every file but today's is immutable history. Keyed on
/// (size, mtime) so an append re-parses exactly one file.
///
/// Mirrors the size+mtime identity `SessionLogService` uses for its on-disk
/// parse cache; this one is in-memory because the window is small and a cold
/// start pays for itself on the second refresh.
///
/// A reference type confined to the main actor: the owning service holds it
/// as a stored property and mutates it across an `await`, which a value type
/// cannot do under Swift 6 exclusivity.
@MainActor
final class ConnectorCallCache {
    private struct Entry {
        let size: Int64
        let mtime: Date
        let calls: [ConnectorCall]
    }

    private var entries: [URL: Entry] = [:]

    /// Files currently memoised. Test seam for eviction.
    var cachedFileCount: Int { entries.count }

    /// Rows for `urls`, parsing only the files whose size or mtime moved since
    /// last time. Entries for files not in `urls` are dropped, so a file that
    /// rolls out of the window stops occupying memory.
    ///
    /// `parse` receives every stale URL at once so the caller can do the work
    /// off-actor in one hop. Identity is snapshotted *before* `parse` runs and
    /// stored with that snapshot: if the file is appended to while parsing,
    /// the stored mtime is the one that was actually read, so the next refresh
    /// sees it as stale again rather than caching a torn read forever.
    ///
    /// A file that cannot be stat'd (deleted mid-flight) contributes nothing
    /// and is not cached — the next refresh will see it again if it returns.
    func calls(
        for urls: [URL],
        parse: (Set<URL>) async -> [URL: [ConnectorCall]]
    ) async -> [ConnectorCall] {
        var identities: [URL: (size: Int64, mtime: Date)] = [:]
        var stale: Set<URL> = []

        for url in urls {
            // `URL.resourceValues` memoises on the URL, which made an appended
            // file look unchanged. Stat through FileManager instead.
            guard let attrs = try? FileManager.default
                    .attributesOfItem(atPath: url.path),
                  let size = attrs[.size] as? Int64 ?? (attrs[.size] as? NSNumber)?.int64Value,
                  let mtime = attrs[.modificationDate] as? Date
            else { continue }
            identities[url] = (size, mtime)
            if let cached = entries[url], cached.size == size, cached.mtime == mtime {
                continue
            }
            stale.insert(url)
        }

        let parsed = stale.isEmpty ? [:] : await parse(stale)

        var next: [URL: Entry] = [:]
        next.reserveCapacity(identities.count)
        var out: [ConnectorCall] = []
        for url in urls {
            guard let id = identities[url] else { continue }
            let entry: Entry
            if stale.contains(url) {
                entry = Entry(size: id.size, mtime: id.mtime, calls: parsed[url] ?? [])
            } else if let cached = entries[url] {
                entry = cached
            } else {
                continue
            }
            next[url] = entry
            out.append(contentsOf: entry.calls)
        }

        entries = next
        return out
    }
}
