import CryptoKit
import Foundation

// The app's model for writing vault files (C10).
//
// Writes the app makes to vault files follow four rules:
//
// 1. **Stale check, by kind of write.**
//    - A *whole-file* write (KB note save, create, schedule save) is refused
//      when the file's bytes differ from what the page loaded: re-applying a
//      full overwrite onto someone else's change would clobber it.
//    - A *targeted* write (an action item addressed by its `[#TAG]`, one
//      frontmatter field of a proposal or wishlist item, a Topics entry) is
//      re-applied onto the current file instead: scoutctl resolves the tag on
//      the bytes it reads, and `GuardedFileWrite` re-reads and reapplies a field
//      rewrite. Only a legacy action item with no tag is addressed by its text,
//      so that one is refused when the day file changed since it was shown.
// 2. **Atomic write.** Bytes go to a temporary file in the same directory and
//    are renamed over the target, so a reader never sees half a file. The
//    target's permissions are kept, and a symlinked file is written through to
//    its target rather than replaced by a regular file.
// 3. **Clear failure.** Failures map to a `WriteProblem` (what happened, how to
//    fix it, whether Retry makes sense, the raw details to copy).
// 4. **Real state after.** Whether the write worked or not, the page reloads
//    from disk, so what's on screen is what's in the file.
//
// A successful write returns a `VaultChange`: for every file it touched, the
// bytes that changed plus SHA-256 hashes of the whole file before and after.
// Undo rebuilds "before" from the file on disk and writes it back; redo
// rebuilds "after". Each runs only while the file hashes to exactly what that
// step expects — if Scout (or anything else) changed the file in between,
// undo is refused with a stale-file problem rather than overwriting the newer
// change. A file that can't be read is never treated as missing.

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
    /// A file that can't be read matches nothing, not even "no file".
    func matchesDisk() -> Bool {
        (try? FileSnapshot.capture(url)).map { $0.data == data } ?? false
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
            // Write through a symlink to its target, and keep the target's
            // permissions: the atomic rename would otherwise swap in a new
            // regular file with default ones.
            let target = url.resolvingSymlinksInPath()
            let mode = (try? fm.attributesOfItem(atPath: target.path))?[.posixPermissions]
            try fm.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: target, options: .atomic)
            if let mode { try fm.setAttributes([.posixPermissions: mode], ofItemAtPath: target.path) }
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

/// How one file changed in a completed write: the common prefix and suffix
/// lengths, the bytes between them on each side, and a hash of each whole
/// side. Keeps only the changed bytes, so a one-line edit to a 2 MB day file
/// costs a line, not two copies of the file.
nonisolated struct FileDelta: Equatable, Sendable {
    struct Side: Equatable, Sendable {
        /// SHA-256 of the whole file, nil when it didn't exist.
        let digest: Data?
        /// The bytes between the common prefix and suffix.
        let middle: Data
        var exists: Bool { digest != nil }
    }

    let url: URL
    let prefix: Int
    let suffix: Int
    let before: Side
    let after: Side

    /// nil when the two snapshots hold the same bytes.
    init?(before b: FileSnapshot, after a: FileSnapshot) {
        precondition(b.url == a.url)
        guard b.data != a.data else { return nil }
        url = a.url
        lineDelta = LineDelta(before: b.data, after: a.data)
        if let bd = b.data, let ad = a.data {
            let p = zip(bd, ad).prefix { $0 == $1 }.count
            let room = min(bd.count, ad.count) - p
            let s = zip(bd.reversed(), ad.reversed()).prefix(room) .prefix { $0 == $1 }.count
            prefix = p
            suffix = s
            before = Side(digest: b.digest, middle: bd.subdata(in: (bd.startIndex + p)..<(bd.endIndex - s)))
            after = Side(digest: a.digest, middle: ad.subdata(in: (ad.startIndex + p)..<(ad.endIndex - s)))
        } else {
            prefix = 0
            suffix = 0
            before = Side(digest: b.digest, middle: b.data ?? Data())
            after = Side(digest: a.digest, middle: a.data ?? Data())
        }
    }

    /// Rebuild the `target` side from `current`, the file as it is now, which
    /// must hash to the `from` side. Throws `.undoStale` otherwise. nil means
    /// "the file shouldn't exist".
    func rebuild(_ target: Side, from expected: Side, current: Data?) throws -> Data? {
        guard current.map({ Data(SHA256.hash(data: $0)) }) == expected.digest else {
            throw VaultWriteError.undoStale(file: url.lastPathComponent)
        }
        guard target.exists else { return nil }
        guard let current else { return target.middle }
        let rebuilt = current.prefix(prefix) + target.middle + current.suffix(suffix)
        guard Data(SHA256.hash(data: rebuilt)) == target.digest else {
            throw VaultWriteError.undoStale(file: url.lastPathComponent)
        }
        return rebuilt
    }

    /// Lines removed and added, between the common leading and trailing
    /// lines of the two sides. A write that should touch one line but shows
    /// more was mixed with someone else's edit, so it must not be byte-undone.
    let lineDelta: LineDelta
}

nonisolated struct LineDelta: Equatable, Sendable {
    let removed: Int
    let added: Int

    init(removed: Int, added: Int) {
        self.removed = removed
        self.added = added
    }

    init(before: Data?, after: Data?) {
        // Lines keep their terminator, so "" → "x\n" is one line added and
        // a missing final newline counts as a change to the last line.
        func lines(_ d: Data?) -> [Data] {
            guard let d else { return [] }
            var out: [Data] = []
            var start = d.startIndex
            for i in d.indices where d[i] == 0x0A {
                out.append(d.subdata(in: start..<(i + 1)))
                start = i + 1
            }
            if start < d.endIndex { out.append(d.subdata(in: start..<d.endIndex)) }
            return out
        }
        let b = lines(before), a = lines(after)
        let p = zip(b, a).prefix { $0 == $1 }.count
        let room = min(b.count, a.count) - p
        let s = zip(b.reversed(), a.reversed()).prefix(room).prefix { $0 == $1 }.count
        self.init(removed: b.count - p - s, added: a.count - p - s)
    }
}

/// A completed write, as how every file it touched changed. The unit of undo:
/// undo rebuilds and restores each file's "before", redo its "after".
nonisolated struct VaultChange: Equatable, Sendable {
    /// Shown in the Edit menu as "Undo <actionName>" — e.g. "Mark Done".
    let actionName: String
    let files: [FileDelta]

    /// Build from before/after snapshots of the same files, keeping only the
    /// files whose bytes actually changed. Returns nil when nothing changed —
    /// a no-op write has nothing to undo.
    init?(actionName: String, before: [FileSnapshot], after: [FileSnapshot]) {
        precondition(before.map(\.url) == after.map(\.url), "before/after must list the same files")
        let files = zip(before, after).compactMap { FileDelta(before: $0, after: $1) }
        guard !files.isEmpty else { return nil }
        self.actionName = actionName
        self.files = files
    }

    /// Restore every file's `before`. Refuses (`.undoStale`) unless every
    /// file still hashes to its `after`; all files are checked and rebuilt
    /// before any is written, so a refusal changes nothing. After writing,
    /// each file is read back and must hash to `before`.
    func undo() throws { try Self.swap(files, toBefore: true) }

    /// Restore `after`, symmetric to `undo()`.
    func redo() throws { try Self.swap(files, toBefore: false) }

    var urls: [URL] { files.map(\.url) }

    private static func swap(_ files: [FileDelta], toBefore: Bool) throws {
        var targets: [(url: URL, data: Data?, digest: Data?)] = []
        for f in files {
            let current = try FileSnapshot.capture(f.url).data
            let (from, to) = toBefore ? (f.after, f.before) : (f.before, f.after)
            targets.append((f.url, try f.rebuild(to, from: from, current: current), to.digest))
        }
        // Deletions last, so a rename's undo writes the old name back before
        // removing the new one: an interrupted undo leaves two copies, never
        // zero.
        let ordered = targets.filter { $0.data != nil } + targets.filter { $0.data == nil }
        for t in ordered {
            try VaultWrite.write(t.data, to: t.url)
        }
        for t in ordered where try FileSnapshot.capture(t.url).digest != t.digest {
            throw VaultWriteError.writeFailed(file: t.url.lastPathComponent,
                                              reason: "the file changed again while Scout was restoring it")
        }
    }
}

nonisolated extension Array where Element == URL {
    /// Snapshot each URL now.
    func snapshots() throws -> [FileSnapshot] { try map(FileSnapshot.capture) }
}
