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
    func save(
        fileURL: URL,
        contents: String,
        baselineContents: String?,
        label: String
    ) async throws {
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
    func delete(fileURL: URL, label: String) async throws {
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
        try ensureInsideKB(fileURL)
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.performRename(
                fileURL: fileURL, newName: newName,
                scoutDirectory: scoutDirectory, gitService: gitService)
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
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
    ) async throws {
        let fm = FileManager.default
        let exists = fm.fileExists(atPath: fileURL.path)
        if exists {
            // Conflict check: what's on disk must still be what the editor
            // loaded. A nil baseline on an existing file means the caller
            // couldn't read it at load — treat as a conflict to be safe.
            guard let baselineContents,
                  let current = try? String(contentsOf: fileURL, encoding: .utf8),
                  current == baselineContents else {
                throw KBWriterError.conflict(file: fileURL.lastPathComponent)
            }
        }
        do {
            try fm.createDirectory(at: fileURL.deletingLastPathComponent(),
                                   withIntermediateDirectories: true)
            try contents.write(to: fileURL, atomically: true, encoding: .utf8)
        } catch {
            throw KBWriterError.writeFailed(error.localizedDescription)
        }
        let rel = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        try await commitOrThrow(gitService, paths: [rel], message: "app: edit \(label)")
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
            try fm.createDirectory(at: directory, withIntermediateDirectories: true)
            try initialContents.write(to: dest, atomically: true, encoding: .utf8)
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
    ) async throws {
        let fm = FileManager.default
        guard fm.fileExists(atPath: fileURL.path) else {
            throw KBWriterError.notFound(fileURL.lastPathComponent)
        }
        let rel = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        do { try fm.removeItem(at: fileURL) }
        catch { throw KBWriterError.writeFailed(error.localizedDescription) }
        // `git add -- <path>` stages the deletion (git ≥ 2.0).
        try await commitOrThrow(gitService, paths: [rel], message: "app: delete \(label)")
    }

    private static func performRename(
        fileURL: URL, newName: String,
        scoutDirectory: URL, gitService: GitServiceProtocol?
    ) async throws -> URL {
        let fm = FileManager.default
        guard fm.fileExists(atPath: fileURL.path) else {
            throw KBWriterError.notFound(fileURL.lastPathComponent)
        }
        // Preserve the original extension if the new name doesn't carry one.
        let originalExt = fileURL.pathExtension
        var fileName = try normalizedFileName(newName, defaultExtension: originalExt)
        if (fileName as NSString).pathExtension.isEmpty && !originalExt.isEmpty {
            fileName += ".\(originalExt)"
        }
        let dest = fileURL.deletingLastPathComponent().appendingPathComponent(fileName)
        if fm.fileExists(atPath: dest.path) {
            throw KBWriterError.alreadyExists(fileName)
        }
        let oldRel = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        let newRel = relativePathInRepo(fileURL: dest, repo: scoutDirectory)
        do { try fm.moveItem(at: fileURL, to: dest) }
        catch { throw KBWriterError.writeFailed(error.localizedDescription) }
        try await commitOrThrow(gitService, paths: [oldRel, newRel], message: "app: rename \(oldRel) → \(newRel)")
        return dest
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

/// Actors can't be `@EnvironmentObject`; wrap for SwiftUI injection.
final class KnowledgeBaseWriterBox: ObservableObject {
    let writer: KnowledgeBaseFileWriter
    init(writer: KnowledgeBaseFileWriter) { self.writer = writer }
}
