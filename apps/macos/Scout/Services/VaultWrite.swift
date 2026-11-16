import CryptoKit
import Foundation

// The app's one model for writing vault files (C10).
//
// Every write the app makes to the vault follows the same four rules:
//
// 1. **Stale check.** Before writing, check the file hasn't changed since the
//    app loaded it. How depends on the write:
//    - A *whole-file* write (KB note save, schedule save) fails on a stale
//      file: re-applying a full overwrite onto someone else's change would
//      clobber it. The caller compares the disk to what it loaded.
//    - A *targeted* write (an action item addressed by its `[#TAG]`, one
//      frontmatter field of a proposal or wishlist item, a Topics entry) is
//      re-applied onto the fresh file: scoutctl resolves the tag on the current
//      bytes, and `GuardedFileWrite` re-reads and reapplies a field rewrite.
//      Only a legacy action item with no tag is addressed by its text, so that
//      one is refused when the day file changed since it was shown.
// 2. **Atomic write.** Bytes go to a temporary file in the same directory and
//    are renamed over the target, so a reader never sees half a file.
// 3. **Clear failure.** Every failure maps to a `WriteProblem` (what happened,
//    how to fix it, whether Retry makes sense, the raw details to copy).
// 4. **Real state after.** Whether the write worked or not, the page reloads
//    from disk, so what's on screen is what's in the file.
//
// A successful write returns a `VaultChange`: the bytes of every file it
// touched, before and after. Undo writes the "before" bytes back and Redo the
// "after" bytes, each only while the disk still holds what that step expects —
// if Scout (or anything else) changed the file in between, undo is refused
// with a stale-file problem rather than overwriting the newer change.

/// One file's bytes at one moment. `data == nil` means the file didn't exist.
nonisolated struct FileSnapshot: Equatable, Sendable {
    let url: URL
    let data: Data?

    var exists: Bool { data != nil }

    /// Read `url` now. A missing file is a snapshot with no data; any other
    /// read failure throws, since "couldn't read" must never be mistaken for
    /// "doesn't exist" (undo would then delete the file).
    static func capture(_ url: URL) throws -> FileSnapshot {
        do {
            return FileSnapshot(url: url, data: try Data(contentsOf: url))
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile {
            return FileSnapshot(url: url, data: nil)
        } catch let error as NSError
            where error.domain == NSPOSIXErrorDomain && error.code == Int(ENOENT) {
            return FileSnapshot(url: url, data: nil)
        } catch {
            throw VaultWriteError.readFailed(file: url.lastPathComponent, reason: error.localizedDescription)
        }
    }

    static func text(_ url: URL, _ text: String?) -> FileSnapshot {
        FileSnapshot(url: url, data: text.map { Data($0.utf8) })
    }

    /// SHA-256 of the bytes, or nil for a missing file.
    var digest: Data? { data.map { Data(SHA256.hash(data: $0)) } }

    /// Whether the file on disk right now holds exactly these bytes.
    func matchesDisk() -> Bool {
        (try? FileSnapshot.capture(url))?.data == data
    }
}

nonisolated enum VaultWriteError: Error, Equatable, Sendable {
    /// The file changed on disk since the app loaded it; the write was refused.
    case stale(file: String)
    /// Undo or redo was refused because the file changed since the write.
    case undoStale(file: String)
    case readFailed(file: String, reason: String)
    case writeFailed(file: String, reason: String)
}

nonisolated enum VaultWrite {
    /// Write `data` to `url` atomically (temp file in the same directory, then
    /// rename), or remove the file when `data` is nil. Creates the parent
    /// directory when needed.
    static func write(_ data: Data?, to url: URL) throws {
        let fm = FileManager.default
        do {
            guard let data else {
                if fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
                return
            }
            try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: url, options: .atomic)
        } catch {
            throw VaultWriteError.writeFailed(file: url.lastPathComponent, reason: error.localizedDescription)
        }
    }

    /// Replace the file at `url` with `data` (nil removes it), but only if the
    /// disk still holds `expected` (nil: the file must not exist). Throws
    /// `.stale` otherwise, leaving the file untouched.
    ///
    /// The check and the rename are not one atomic step; the window is the
    /// time between two syscalls, the same window `GuardedFileWrite` accepts.
    static func replace(_ url: URL, expecting expected: Data?, with data: Data?) throws {
        let current = try FileSnapshot.capture(url)
        guard current.data == expected else {
            throw VaultWriteError.stale(file: url.lastPathComponent)
        }
        try write(data, to: url)
    }
}

/// A completed write, as the bytes of every file it touched before and after.
/// The unit of undo: undo restores `before`, redo restores `after`.
nonisolated struct VaultChange: Equatable, Sendable {
    /// Shown in the Edit menu as "Undo <actionName>" — e.g. "Mark Done".
    let actionName: String
    let before: [FileSnapshot]
    let after: [FileSnapshot]

    /// Build from before/after snapshots of the same files, keeping only the
    /// files whose bytes actually changed. Returns nil when nothing changed —
    /// a no-op write has nothing to undo.
    init?(actionName: String, before: [FileSnapshot], after: [FileSnapshot]) {
        precondition(before.map(\.url) == after.map(\.url), "before/after must list the same files")
        let changed = zip(before, after).filter { $0.data != $1.data }
        guard !changed.isEmpty else { return nil }
        self.actionName = actionName
        self.before = changed.map(\.0)
        self.after = changed.map(\.1)
    }

    /// Restore `before`. Refuses (`.undoStale`) unless every file still holds
    /// its `after` bytes; checks all files first so a refusal changes nothing.
    func undo() throws { try Self.swap(from: after, to: before) }

    /// Restore `after`, symmetric to `undo()`.
    func redo() throws { try Self.swap(from: before, to: after) }

    var urls: [URL] { after.map(\.url) }

    private static func swap(from expected: [FileSnapshot], to target: [FileSnapshot]) throws {
        for snap in expected where !snap.matchesDisk() {
            throw VaultWriteError.undoStale(file: snap.url.lastPathComponent)
        }
        // Deletions last, so a rename's undo writes the old name back before
        // removing the new one: an interrupted undo leaves two copies, never
        // zero.
        let ordered = target.filter(\.exists) + target.filter { !$0.exists }
        for snap in ordered {
            try VaultWrite.write(snap.data, to: snap.url)
        }
    }
}

nonisolated extension Array where Element == URL {
    /// Snapshot each URL now.
    func snapshots() throws -> [FileSnapshot] { try map(FileSnapshot.capture) }
}
