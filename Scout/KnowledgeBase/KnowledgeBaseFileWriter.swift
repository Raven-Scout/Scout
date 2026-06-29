import Combine
import Foundation

enum KBWriterError: Error, Equatable {
    case emptyName
    case alreadyExists(String)
    case notFound(String)
    case readFailed(String)
    case writeFailed(String)
    /// The file changed on disk since the editor loaded it (a scout-plugin
    /// session or another editor wrote it). Surfaced rather than clobbered so
    /// the user can reload and reconcile.
    case conflict(file: String)
    case outsideKnowledgeBase(String)
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
        self.scoutDirectory = scoutDirectory
        self.gitService = gitService
    }

    /// Overwrite `fileURL` with `contents`, but only if its modification date
    /// still matches `baseline` (the mtime captured when the editor loaded it).
    /// A `nil` baseline means "the file didn't exist at load" — used when saving
    /// a freshly created note. Commits the single path on success.
    func save(
        fileURL: URL,
        contents: String,
        baseline: Date?,
        label: String
    ) async throws {
        try ensureInsideKB(fileURL)
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.performSave(
                fileURL: fileURL, contents: contents, baseline: baseline,
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
            .standardizedFileURL.path + "/"
        let resolved = url.standardizedFileURL.path
        if !(resolved + "/").hasPrefix(kbRoot) && resolved + "/" != kbRoot {
            throw KBWriterError.outsideKnowledgeBase(url.lastPathComponent)
        }
    }

    // MARK: - perform (off-actor)

    private static func performSave(
        fileURL: URL, contents: String, baseline: Date?, label: String,
        scoutDirectory: URL, gitService: GitServiceProtocol?
    ) async throws {
        let fm = FileManager.default
        let exists = fm.fileExists(atPath: fileURL.path)
        if exists {
            // Conflict check: the file must not have changed since the editor
            // captured `baseline`. A nil baseline on an existing file means the
            // caller couldn't read the mtime — treat as a conflict to be safe.
            guard let baseline else { throw KBWriterError.conflict(file: fileURL.lastPathComponent) }
            let current = GuardedFileWrite.fsModificationDate(fileURL)
            if let current, abs(current.timeIntervalSince(baseline)) > 0.0005 {
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
        try? await gitService?.commitPaths([rel], message: "app: edit \(label)")
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
        try? await gitService?.commitPaths([rel], message: "app: create \(rel)")
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
        try? await gitService?.commitPaths([rel], message: "app: delete \(label)")
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
        try? await gitService?.commitPaths([oldRel, newRel], message: "app: rename \(oldRel) → \(newRel)")
        return dest
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
        let full = fileURL.standardizedFileURL.path
        let prefix = repo.standardizedFileURL.path + "/"
        return full.hasPrefix(prefix) ? String(full.dropFirst(prefix.count)) : fileURL.lastPathComponent
    }
}

/// Actors can't be `@EnvironmentObject`; wrap for SwiftUI injection.
final class KnowledgeBaseWriterBox: ObservableObject {
    let writer: KnowledgeBaseFileWriter
    init(writer: KnowledgeBaseFileWriter) { self.writer = writer }
}
