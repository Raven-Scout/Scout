import Combine
import Foundation

/// A status change the app can write back to a reply-draft file. The app never
/// sends — it only flips `status:` in the markdown.
enum DraftAction: Sendable, Equatable {
    /// The user sent the reply himself.
    case markSent
    /// The reply is no longer needed.
    case dismiss
    /// Move a resolved draft back to `draft`.
    case reopen

    /// Canonical lowercase status word written into the file — must match the
    /// scout-plugin `status:` contract exactly so a re-read round-trips.
    var status: DraftStatus {
        switch self {
        case .markSent: return .sent
        case .dismiss:  return .dismissed
        case .reopen:   return .draft
        }
    }

    /// Verb used in the git commit message.
    var verb: String {
        switch self {
        case .markSent: return "mark-sent"
        case .dismiss:  return "dismiss"
        case .reopen:   return "reopen"
        }
    }
}

enum ReplyDraftsWriterError: Error, Equatable {
    /// The file had no `---` YAML frontmatter to carry a `status:` field.
    case frontmatterNotFound(file: String)
    /// Frontmatter was present but had no `status:` line to replace.
    case statusFieldNotFound(file: String)
    /// The `[TBD: …]` marker being filled was no longer in the file — the draft
    /// changed under us. Surfaced rather than reported as a successful fill,
    /// which would silently discard what the user typed.
    case placeholderNotFound(file: String)
    case readFailed(String)
    case writeFailed(String)
}

/// Serializes reply-draft status mutations to per-file drafts in `drafts/`.
///
/// There is no `scoutctl` command for drafts — they are plain markdown that
/// Scout sessions read and write directly — so the app edits the file in place:
/// replace the `status:` value in the file's YAML frontmatter, write
/// atomically, then commit just that file to the vault's git. The status word
/// (`sent` / `dismissed` / `draft`) is what Scout keys on. Submissions are
/// strictly serialized so two quick clicks can't interleave. The app **never**
/// sends a message or creates a native draft — flipping the status field is the
/// only side effect.
actor ReplyDraftsWriter {
    private let scoutDirectory: URL
    private let gitService: GitServiceProtocol?

    /// Tail of the serial task chain (same pattern as `ProposalsWriter`): each
    /// submission awaits the previous one before running.
    private var tail: Task<Void, Never>?

    init(scoutDirectory: URL, gitService: GitServiceProtocol?) {
        self.scoutDirectory = scoutDirectory
        self.gitService = gitService
    }

    /// Apply a status change to the draft at `fileURL`. `label` is used only for
    /// the git commit message. Returns after the file is written and the git
    /// commit (best-effort) completes.
    func apply(_ action: DraftAction, fileURL: URL, label: String) async throws {
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.perform(
                action: action,
                fileURL: fileURL,
                label: label,
                scoutDirectory: scoutDirectory,
                gitService: gitService
            )
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    /// Fill one `[TBD: …]` placeholder in the draft body with the user's value,
    /// writing it into the file so the reply reads cleanly. `occurrence` picks
    /// which instance of an identical marker to replace (see
    /// ``DraftInput/occurrence``). Status is untouched (the draft stays a
    /// draft). `label` is used for the git commit message.
    ///
    /// Throws ``ReplyDraftsWriterError/placeholderNotFound(file:)`` when the
    /// marker is gone: a no-op here means the draft changed under us, and
    /// reporting it as success would make the caller discard the typed value.
    func fill(placeholder: String, occurrence: Int, value: String, fileURL: URL, label: String) async throws {
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            let didWrite: Bool
            do {
                didWrite = try GuardedFileWrite.apply(to: fileURL) { text in
                    Self.fillPlaceholder(
                        text: text, placeholder: placeholder, occurrence: occurrence, value: value
                    )
                }
            } catch let e as GuardedFileWrite.Failure {
                switch e {
                case .read(let m): throw ReplyDraftsWriterError.readFailed(m)
                case .write(let m): throw ReplyDraftsWriterError.writeFailed(m)
                case .conflictPersisted:
                    throw ReplyDraftsWriterError.writeFailed("\(fileURL.lastPathComponent) changed repeatedly under concurrent writes")
                }
            }
            guard didWrite else {
                throw ReplyDraftsWriterError.placeholderNotFound(file: fileURL.lastPathComponent)
            }
            let rel = Self.relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
            try? await gitService?.commitPaths([rel], message: "app: fill input in reply draft \(label)")
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    /// Replace the `occurrence`-th (zero-based) instance of `placeholder` with
    /// `value`. Matching by position rather than "the first one" is what lets
    /// two identical `[TBD: …]` markers be filled independently — replacing the
    /// first every time made the second unfillable. Returns the text unchanged
    /// when that instance isn't present (the caller turns that into
    /// ``ReplyDraftsWriterError/placeholderNotFound(file:)``).
    static func fillPlaceholder(
        text: String, placeholder: String, occurrence: Int, value: String
    ) -> String {
        guard !placeholder.isEmpty, occurrence >= 0 else { return text }
        var searchStart = text.startIndex
        var remaining = occurrence
        while let range = text.range(of: placeholder, range: searchStart..<text.endIndex) {
            if remaining == 0 {
                return text.replacingCharacters(in: range, with: value)
            }
            remaining -= 1
            searchStart = range.upperBound
        }
        return text
    }

    private static func perform(
        action: DraftAction,
        fileURL: URL,
        label: String,
        scoutDirectory: URL,
        gitService: GitServiceProtocol?
    ) async throws {
        let newStatusValue = action.status.fileValue

        // Read-modify-write guarded against a concurrent Scout write clobbering
        // the file in our read→write window (same guard as ProposalsWriter).
        let didWrite: Bool
        do {
            didWrite = try GuardedFileWrite.apply(to: fileURL) { text in
                try rewriteFrontmatterStatus(
                    text: text,
                    newStatusValue: newStatusValue,
                    file: fileURL.lastPathComponent
                )
            }
        } catch let e as GuardedFileWrite.Failure {
            switch e {
            case .read(let m): throw ReplyDraftsWriterError.readFailed(m)
            case .write(let m): throw ReplyDraftsWriterError.writeFailed(m)
            case .conflictPersisted:
                throw ReplyDraftsWriterError.writeFailed("\(fileURL.lastPathComponent) changed repeatedly under concurrent writes")
            }
        }

        // Nothing to do if the status is already exactly what we'd write.
        guard didWrite else { return }

        let relativePath = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        try? await gitService?.commitPaths(
            [relativePath],
            message: "app: \(action.verb) reply draft \(label)"
        )
    }

    // MARK: - Pure rewrite (unit-tested directly)

    /// Replace the `status:` value inside the file's YAML frontmatter. Only that
    /// one line changes — the body and every other frontmatter field are left
    /// byte-for-byte identical, and the line's leading indentation is preserved.
    /// Throws if there is no frontmatter or no `status:` field within it.
    static func rewriteFrontmatterStatus(
        text: String,
        newStatusValue: String,
        file: String
    ) throws -> String {
        var lines = text.components(separatedBy: "\n")
        guard let first = lines.first,
              first.trimmingCharacters(in: .whitespaces) == "---" else {
            throw ReplyDraftsWriterError.frontmatterNotFound(file: file)
        }

        // Locate the closing fence FIRST. Scanning for `status:` without one
        // would run past the end of the frontmatter and rewrite a `status:`
        // line in the reply body — the file only looks like it has frontmatter.
        // The writer re-reads the file at write time, so it can see a truncated
        // version the parser never accepted.
        guard let fence = lines.dropFirst().firstIndex(where: {
            $0.trimmingCharacters(in: .whitespaces) == "---"
        }) else {
            throw ReplyDraftsWriterError.frontmatterNotFound(file: file)
        }

        for i in 1..<fence {
            guard let colon = lines[i].firstIndex(of: ":") else { continue }
            let key = lines[i][..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            if key == "status" {
                let leading = String(lines[i].prefix(while: { $0 == " " || $0 == "\t" }))
                lines[i] = "\(leading)status: \(newStatusValue)"
                return lines.joined(separator: "\n")
            }
        }
        throw ReplyDraftsWriterError.statusFieldNotFound(file: file)
    }

    // MARK: - Helpers

    private static func relativePathInRepo(fileURL: URL, repo: URL) -> String {
        let filePath = fileURL.standardizedFileURL.path
        let repoPath = repo.standardizedFileURL.path
        if filePath.hasPrefix(repoPath + "/") {
            return String(filePath.dropFirst(repoPath.count + 1))
        }
        return fileURL.lastPathComponent
    }
}

/// A boxed writer — actors can't be stored directly in `@EnvironmentObject`,
/// but a class holding the actor can. Mirrors `ProposalsWriterBox`.
final class ReplyDraftsWriterBox: ObservableObject {
    let writer: ReplyDraftsWriter
    init(writer: ReplyDraftsWriter) { self.writer = writer }
}
