import Combine
import Foundation

/// A proposal decision the app can write back to a proposal file.
enum ProposalDecision: Sendable, Equatable {
    case approve
    case decline

    /// Leading status word — what a dreaming run keys on.
    var statusWord: String { self == .approve ? "Approved" : "Rejected" }
    /// Verb used in the git commit message.
    var verb: String { self == .approve ? "approve" : "decline" }
}

enum ProposalsWriterError: Error, Equatable {
    /// The file had no `---` YAML frontmatter to carry a `status:` field.
    case frontmatterNotFound(file: String)
    /// Frontmatter was present but had no `status:` line to replace.
    case statusFieldNotFound(file: String)
    case readFailed(String)
    case writeFailed(String)
}

/// Serializes proposal status mutations to per-file proposals in
/// `dreaming-proposals/`.
///
/// There is no `scoutctl` command for proposals — they are plain markdown that
/// dreaming sessions read and write directly — so the app edits the file in
/// place: replace the `status:` value in the file's YAML frontmatter, write
/// atomically, then commit just that file to the vault's git. The leading
/// status word (`Approved` / `Rejected`) is what the next dreaming run keys on.
/// Submissions are strictly serialized so two quick clicks can't interleave.
actor ProposalsWriter {
    private let scoutDirectory: URL
    private let gitService: GitServiceProtocol?
    private let now: @Sendable () -> Date

    /// Tail of the serial task chain (same pattern as `ActionItemsWriter`):
    /// each submission awaits the previous one before running.
    private var tail: Task<Void, Never>?

    init(
        scoutDirectory: URL,
        gitService: GitServiceProtocol?,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.scoutDirectory = scoutDirectory
        self.gitService = gitService
        self.now = now
    }

    /// Apply a decision to the proposal at `fileURL`. `label` is used only for
    /// the git commit message. Returns after the file is written and the git
    /// commit (best-effort) completes.
    func decide(_ decision: ProposalDecision, fileURL: URL, label: String) async throws {
        let previous = tail
        let task = Task { [scoutDirectory, gitService, now] in
            _ = await previous?.value
            return try await Self.perform(
                decision: decision,
                fileURL: fileURL,
                label: label,
                scoutDirectory: scoutDirectory,
                gitService: gitService,
                now: now
            )
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    private static func perform(
        decision: ProposalDecision,
        fileURL: URL,
        label: String,
        scoutDirectory: URL,
        gitService: GitServiceProtocol?,
        now: @Sendable () -> Date
    ) async throws {
        let text: String
        do {
            text = try String(contentsOf: fileURL, encoding: .utf8)
        } catch {
            throw ProposalsWriterError.readFailed(error.localizedDescription)
        }

        let stamp = isoDate(now())
        let newStatusValue = "\(decision.statusWord) (\(stamp), via Scout app)"
        let updated = try rewriteFrontmatterStatus(
            text: text,
            newStatusValue: newStatusValue,
            file: fileURL.lastPathComponent
        )

        // Nothing to do if the status is already exactly what we'd write.
        guard updated != text else { return }

        do {
            try updated.write(to: fileURL, atomically: true, encoding: .utf8)
        } catch {
            throw ProposalsWriterError.writeFailed(error.localizedDescription)
        }

        let relativePath = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        try? await gitService?.commitPaths(
            [relativePath],
            message: "app: \(decision.verb) proposal \(label)"
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
            throw ProposalsWriterError.frontmatterNotFound(file: file)
        }

        var i = 1
        while i < lines.count {
            // Closing fence ends the frontmatter without finding `status:`.
            if lines[i].trimmingCharacters(in: .whitespaces) == "---" { break }
            if let colon = lines[i].firstIndex(of: ":") {
                let key = lines[i][..<colon].trimmingCharacters(in: .whitespaces).lowercased()
                if key == "status" {
                    let leading = String(lines[i].prefix(while: { $0 == " " || $0 == "\t" }))
                    lines[i] = "\(leading)status: \(newStatusValue)"
                    return lines.joined(separator: "\n")
                }
            }
            i += 1
        }
        throw ProposalsWriterError.statusFieldNotFound(file: file)
    }

    // MARK: - Helpers

    private static func isoDate(_ date: Date) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"
        fmt.locale = Locale(identifier: "en_US_POSIX")
        return fmt.string(from: date)
    }

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
/// but a class holding the actor can. Mirrors `ActionItemsWriterBox`.
final class ProposalsWriterBox: ObservableObject {
    let writer: ProposalsWriter
    init(writer: ProposalsWriter) { self.writer = writer }
}
