import Combine
import Foundation

enum KBWriterError: Error, Equatable {
    case emptyName
    case alreadyExists(String)
    case notFound(String)
    case writeFailed(String)
    /// The file changed on disk since the editor loaded it (a scout-plugin
    /// session or another editor wrote it). Surfaced rather than clobbered so
    /// the user can reload and reconcile.
    case conflict(file: String)
    case outsideKnowledgeBase(String)
    /// The file operation succeeded but the scoped git commit didn't (e.g.
    /// `.git/index.lock` contention while a plugin session runs). Surfaced so
    /// the user knows the change is on disk but uncommitted — a later plugin
    /// sync could otherwise clobber or resurrect it silently.
    case commitFailed(String)

    /// User-facing alert message.
    var userMessage: String {
        switch self {
        case .emptyName: return "The name can't be empty."
        case .alreadyExists(let n): return "A file named \(n) already exists."
        case .notFound(let n): return "\(n) no longer exists."
        case .writeFailed(let m): return m
        case .conflict(let f): return "\(f) changed on disk."
        case .outsideKnowledgeBase(let n): return "\(n) is outside the knowledge base."
        case .commitFailed(let m): return "The change was written, but the git commit failed: \(m)"
        }
    }

    /// User-facing message for any error a writer call can throw.
    static func message(for error: Error) -> String {
        (error as? KBWriterError)?.userMessage ?? error.localizedDescription
    }
}

/// Serializes knowledge-base file mutations (save, create, delete, rename) and
/// git-commits each change scoped to the touched path(s).
///
/// Unlike `PerFileItemWriter` (which rewrites a single frontmatter field via
/// `GuardedFileWrite`'s re-apply-on-conflict strategy), the KB editor saves the
/// *whole* file. Re-applying a full-document overwrite onto a concurrently
/// changed file would silently clobber the other writer — so `save` uses a
/// baseline-mtime guard that *fails* on conflict instead of merging.
actor KnowledgeBaseFileWriter {
    private let scoutDirectory: URL
    private let gitService: GitServiceProtocol?
    private var tail: Task<Void, Never>?

    init(scoutDirectory: URL, gitService: GitServiceProtocol?) {
        // Resolve symlinks so the in-KB guard and repo-relative paths match the
        // tree's symlink-resolved file URLs (see KnowledgeBaseService.init).
        self.scoutDirectory = scoutDirectory.resolvingSymlinksInPath()
        self.gitService = gitService
    }

    /// Overwrite `fileURL` with `contents`, but only if what's on disk still
    /// matches `baselineContents` (the text captured when the editor loaded
    /// it). Comparing content — not mtime — stays correct on filesystems with
    /// coarse timestamp granularity. A `nil` baseline means "the file didn't
    /// exist (or wasn't readable) at load". Commits the single path on success.
    ///
    /// Returns the bytes before and after for undo (nil for an unchanged save).
    /// A `.commitFailed` error still means the file was written; the change
    /// is then not undoable from the app.
    @discardableResult
    func save(
        fileURL: URL,
        contents: String,
        baselineContents: String?,
        label: String
    ) async throws -> VaultChange? {
        try ensureInsideKB(fileURL)
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.performSave(
                fileURL: fileURL, contents: contents, baselineContents: baselineContents,
                label: label, scoutDirectory: scoutDirectory, gitService: gitService)
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    /// Create a new `.md` note named `name` (slug, extension optional) inside
    /// `directory`. Fails if a file of that name already exists. Returns the new
    /// file's URL.
    @discardableResult
    func createFile(in directory: URL, name: String, initialContents: String) async throws -> URL {
        try ensureInsideKB(directory)
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.performCreate(
                directory: directory, name: name, initialContents: initialContents,
                scoutDirectory: scoutDirectory, gitService: gitService)
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    /// Delete `fileURL` and commit the removal.
    /// Returns the deleted bytes for undo, which writes the note back.
    @discardableResult
    func delete(fileURL: URL, label: String) async throws -> VaultChange? {
        try ensureInsideKB(fileURL)
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.performDelete(
                fileURL: fileURL, label: label,
                scoutDirectory: scoutDirectory, gitService: gitService)
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    /// Rename `fileURL` to `newName` (within the same directory). Returns the new
    /// URL. Fails if the destination already exists.
    @discardableResult
    func rename(fileURL: URL, to newName: String) async throws -> URL {
        try await renameReturningChange(fileURL: fileURL, to: newName).url
    }

    /// `rename`, plus both paths' bytes before and after for undo (which moves
    /// the note back).
    func renameReturningChange(fileURL: URL, to newName: String) async throws -> (url: URL, change: VaultChange?) {
        let result = try await renameUpdatingLinks(fileURL: fileURL, to: newName, links: nil)
        return (result.url, result.change)
    }

    /// `renameReturningChange`, also pointing the links in `links.notes` at
    /// the new name (UI audit #75). The rewrites land in the same commit and
    /// the same undo step as the move, so undo puts every note back. Each
    /// note is re-read and rewritten only if it's unchanged since that read;
    /// a note that can't be read or changed meanwhile is reported, not forced.
    func renameUpdatingLinks(
        fileURL: URL, to newName: String, links: KBRenameLinkPlan?
    ) async throws -> (url: URL, change: VaultChange?, links: KBRenameLinkReport) {
        try ensureInsideKB(fileURL)
        for url in links?.notes ?? [] { try ensureInsideKB(url) }
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.performRename(
                fileURL: fileURL, newName: newName, links: links,
                scoutDirectory: scoutDirectory, gitService: gitService)
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    /// Undo or redo a change this writer made, in its serial queue. Only
    /// paths inside the knowledge base are ever restored.
    func restore(_ change: VaultChange, _ direction: UndoDirection) async throws {
        for url in change.urls { try ensureInsideKB(url) }
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            try await change.apply(direction, git: gitService, repo: scoutDirectory)
        }
        tail = Task { _ = try? await task.value }
        try await task.value
    }

    // MARK: - Guard

    /// Reject any path that resolves outside `scoutDirectory/knowledge-base` —
    /// defense against a crafted selection escaping the KB root.
    private func ensureInsideKB(_ url: URL) throws {
        let kbRoot = scoutDirectory.appendingPathComponent("knowledge-base")
            .resolvingSymlinksInPath().path + "/"
        let resolved = url.resolvingSymlinksInPath().path + "/"
        guard resolved.hasPrefix(kbRoot) else {
            throw KBWriterError.outsideKnowledgeBase(url.lastPathComponent)
        }
    }

    // MARK: - perform (off-actor)

    private static func performSave(
        fileURL: URL, contents: String, baselineContents: String?, label: String,
        scoutDirectory: URL, gitService: GitServiceProtocol?
    ) async throws -> VaultChange? {
        // Conflict check: the bytes on disk must still be exactly what the
        // editor loaded. Bytes, not `String`s: `String ==` treats NFC and NFD
        // spellings of the same text as equal. A nil baseline on an existing
        // file means the caller couldn't read it at load, and a file that
        // can't be read now is not "missing": both are conflicts.
        let onDisk: Data?
        do { onDisk = try FileSnapshot.capture(fileURL).data }
        catch { throw KBWriterError.conflict(file: fileURL.lastPathComponent) }
        if let onDisk, !Self.bytes(onDisk, match: baselineContents) {
            throw KBWriterError.conflict(file: fileURL.lastPathComponent)
        }
        // Keep a UTF-8 byte-order mark the file had: the editor's text was
        // decoded without it.
        var written = Data(contents.utf8)
        if let onDisk, onDisk.starts(with: Self.utf8BOM), !written.starts(with: Self.utf8BOM) {
            written = Self.utf8BOM + written
        }
        do {
            try VaultWrite.replace(fileURL, expecting: onDisk, with: written)
        } catch VaultWriteError.stale {
            throw KBWriterError.conflict(file: fileURL.lastPathComponent)
        } catch {
            throw KBWriterError.writeFailed(error.localizedDescription)
        }
        let rel = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        try await commitOrThrow(gitService, paths: [rel], message: "app: edit \(label)")
        return VaultChange(actionName: "Edit Note",
                           before: [FileSnapshot(url: fileURL, data: onDisk)],
                           after: [FileSnapshot(url: fileURL, data: written)])
    }

    private static func performCreate(
        directory: URL, name: String, initialContents: String,
        scoutDirectory: URL, gitService: GitServiceProtocol?
    ) async throws -> URL {
        let fileName = try normalizedFileName(name)
        let dest = directory.appendingPathComponent(fileName)
        let fm = FileManager.default
        if fm.fileExists(atPath: dest.path) {
            throw KBWriterError.alreadyExists(fileName)
        }
        do {
            try VaultWrite.replace(dest, expecting: nil, with: Data(initialContents.utf8))
        } catch VaultWriteError.stale {
            throw KBWriterError.alreadyExists(fileName)
        } catch {
            throw KBWriterError.writeFailed(error.localizedDescription)
        }
        let rel = relativePathInRepo(fileURL: dest, repo: scoutDirectory)
        try await commitOrThrow(gitService, paths: [rel], message: "app: create \(rel)")
        return dest
    }

    private static func performDelete(
        fileURL: URL, label: String,
        scoutDirectory: URL, gitService: GitServiceProtocol?
    ) async throws -> VaultChange? {
        let fm = FileManager.default
        guard fm.fileExists(atPath: fileURL.path) else {
            throw KBWriterError.notFound(fileURL.lastPathComponent)
        }
        let before = try? FileSnapshot.capture(fileURL)
        let rel = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        do { try fm.removeItem(at: fileURL) }
        catch { throw KBWriterError.writeFailed(error.localizedDescription) }
        // `git add -- <path>` stages the deletion (git ≥ 2.0).
        try await commitOrThrow(gitService, paths: [rel], message: "app: delete \(label)")
        return before.flatMap {
            VaultChange(actionName: "Delete Note", before: [$0], after: [FileSnapshot(url: fileURL, data: nil)])
        }
    }

    private static func performRename(
        fileURL: URL, newName: String, links: KBRenameLinkPlan?,
        scoutDirectory: URL, gitService: GitServiceProtocol?
    ) async throws -> (url: URL, change: VaultChange?, links: KBRenameLinkReport) {
        let fm = FileManager.default
        guard fm.fileExists(atPath: fileURL.path) else {
            throw KBWriterError.notFound(fileURL.lastPathComponent)
        }
        let dest = try renameDestination(for: fileURL, to: newName)
        let fileName = dest.lastPathComponent
        if fm.fileExists(atPath: dest.path) {
            throw KBWriterError.alreadyExists(fileName)
        }
        let oldRel = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        let newRel = relativePathInRepo(fileURL: dest, repo: scoutDirectory)
        let moved = try? FileSnapshot.capture(fileURL)
        do { try fm.moveItem(at: fileURL, to: dest) }
        catch { throw KBWriterError.writeFailed(error.localizedDescription) }

        // The move succeeded: now the links. Nothing here throws, so a note
        // that can't be updated never strands the rename half-done.
        var report = KBRenameLinkReport(notesSkipped: links?.skipped ?? [])
        var linkBefore: [FileSnapshot] = []
        var linkAfter: [FileSnapshot] = []
        if let links {
            let newStem = (fileName as NSString).deletingPathExtension
            let kbPrefix = "knowledge-base/"
            let newKBPath = ((newRel.hasPrefix(kbPrefix) ? String(newRel.dropFirst(kbPrefix.count)) : newRel)
                             as NSString).deletingPathExtension
            for url in links.notes where url.standardizedFileURL != fileURL.standardizedFileURL {
                let source = KnowledgeBaseService.relativePath(of: url, in: scoutDirectory)
                guard let snapshot = try? FileSnapshot.capture(url), let data = snapshot.data,
                      let decoded = decodeUTF8(data) else {
                    report.notesSkipped.append(source)
                    continue
                }
                let result = KBLinkRewrite.rewrite(decoded.text, source: source, oldPath: links.oldPath,
                                                   newStem: newStem, newKBPath: newKBPath, index: links.index)
                guard result.count > 0 else { continue }
                let written = (decoded.bom ? utf8BOM : Data()) + Data(result.text.utf8)
                do {
                    try VaultWrite.replace(url, expecting: data, with: written)
                } catch {
                    report.notesSkipped.append(source)
                    continue
                }
                linkBefore.append(snapshot)
                linkAfter.append(FileSnapshot(url: url, data: written))
                report.linksUpdated += result.count
                report.notesUpdated += 1
            }
            report.notesSkipped.sort()
        }

        let linkPaths = linkBefore.map { relativePathInRepo(fileURL: $0.url, repo: scoutDirectory) }
        var message = "app: rename \(oldRel) → \(newRel)"
        if report.notesUpdated > 0 {
            message += " (updated \(report.linksUpdated) link\(report.linksUpdated == 1 ? "" : "s")"
                + " in \(report.notesUpdated) note\(report.notesUpdated == 1 ? "" : "s"))"
        }
        try await commitOrThrow(gitService, paths: [oldRel, newRel] + linkPaths, message: message)
        let change = moved.flatMap {
            VaultChange(actionName: "Rename Note",
                        before: [$0, FileSnapshot(url: dest, data: nil)] + linkBefore,
                        after: [FileSnapshot(url: fileURL, data: nil), FileSnapshot(url: dest, data: $0.data)] + linkAfter)
        }
        return (dest, change, report)
    }

    /// The text of a UTF-8 file, and whether it started with a byte-order
    /// mark (dropped from the text, put back on write). nil if it isn't UTF-8.
    static func decodeUTF8(_ data: Data) -> (text: String, bom: Bool)? {
        let bom = data.starts(with: utf8BOM)
        let body = bom ? data.dropFirst(utf8BOM.count) : data[...]
        return String(data: Data(body), encoding: .utf8).map { ($0, bom) }
    }

    /// Where `rename(fileURL:to:)` moves the file: same directory, the new
    /// name normalized, the original extension kept when the new name has
    /// none. Callers use it to follow a rename whose commit failed, since the
    /// file has moved even though `rename` threw.
    static func renameDestination(for fileURL: URL, to newName: String) throws -> URL {
        let originalExt = fileURL.pathExtension
        var fileName = try normalizedFileName(newName, defaultExtension: originalExt)
        if (fileName as NSString).pathExtension.isEmpty && !originalExt.isEmpty {
            fileName += ".\(originalExt)"
        }
        return fileURL.deletingLastPathComponent().appendingPathComponent(fileName)
    }

    /// Commit the given paths, converting a git failure into `.commitFailed`
    /// instead of discarding it — the file op already succeeded, so callers
    /// treat this error as "written but uncommitted" and tell the user.
    private static func commitOrThrow(
        _ gitService: GitServiceProtocol?, paths: [String], message: String
    ) async throws {
        guard let gitService else { return }
        do { try await gitService.commitPaths(paths, message: message) }
        catch { throw KBWriterError.commitFailed(error.localizedDescription) }
    }

    // MARK: - pure helpers

    static let utf8BOM = Data([0xEF, 0xBB, 0xBF])

    /// Whether `disk` holds exactly the text the editor loaded. Byte-exact
    /// (`String ==` would equate NFC and NFD spellings), except for a UTF-8
    /// byte-order mark: decoding drops it, so a note starting with one would
    /// otherwise never match its own text and could never be saved.
    static func bytes(_ disk: Data, match baseline: String?) -> Bool {
        guard let baseline else { return false }
        let text = Data(baseline.utf8)
        return disk == text || (disk.starts(with: utf8BOM) && disk.dropFirst(utf8BOM.count) == text)
    }

    /// Validate and normalize a user-entered file name. Rejects empty names and
    /// path separators (no creating files outside the chosen directory). Adds a
    /// `.md` extension when none is present.
    static func normalizedFileName(_ raw: String, defaultExtension: String = "md") throws -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw KBWriterError.emptyName }
        guard !trimmed.contains("/"), !trimmed.contains("\\"), trimmed != ".", trimmed != ".." else {
            throw KBWriterError.writeFailed("Name cannot contain path separators")
        }
        if (trimmed as NSString).pathExtension.isEmpty {
            return "\(trimmed).\(defaultExtension)"
        }
        return trimmed
    }

    static func relativePathInRepo(fileURL: URL, repo: URL) -> String {
        let full = fileURL.resolvingSymlinksInPath().path
        let prefix = repo.resolvingSymlinksInPath().path + "/"
        return full.hasPrefix(prefix) ? String(full.dropFirst(prefix.count)) : fileURL.lastPathComponent
    }
}

/// The links a rename should repoint (UI audit #75): built from the index the
/// rename sheet showed, minus notes with unsaved edits.
nonisolated struct KBRenameLinkPlan: Sendable {
    /// The index before the rename, which decides which links point at the note.
    let index: KBIndex
    /// The renamed note's path relative to the Scout folder, as the index has it.
    let oldPath: String
    /// The notes to update.
    let notes: [URL]
    /// Notes that link here but were left alone up front (unsaved edits are
    /// open in them), relative paths.
    let skipped: [String]

    /// The plan for renaming `oldPath`: every note linking to it, except
    /// those with unsaved edits, which are listed as skipped instead.
    @MainActor
    static func make(renaming oldPath: String, index: KBIndex, scoutDirectory: URL,
                     drafts: KBDraftStore) -> KBRenameLinkPlan {
        var notes: [URL] = []
        var skipped: [String] = []
        for inbound in KBLinkRewrite.inboundNotes(to: oldPath, in: index) {
            let url = scoutDirectory.appendingPathComponent(inbound.path)
            if drafts.draft(for: url)?.isDirty == true { skipped.append(inbound.path) } else { notes.append(url) }
        }
        return KBRenameLinkPlan(index: index, oldPath: oldPath, notes: notes, skipped: skipped.sorted())
    }
}

/// Actors can't be `@EnvironmentObject`; wrap for SwiftUI injection.
final class KnowledgeBaseWriterBox: ObservableObject {
    let writer: KnowledgeBaseFileWriter
    init(writer: KnowledgeBaseFileWriter) { self.writer = writer }
}
