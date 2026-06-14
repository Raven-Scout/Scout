import Combine
import Foundation

/// A proposal decision the app can write back to the file.
enum ProposalDecision: Sendable, Equatable {
    case approve
    case decline

    /// Leading status word — what a dreaming run keys on.
    var statusWord: String { self == .approve ? "Approved" : "Rejected" }
    /// Verb used in the git commit message.
    var verb: String { self == .approve ? "approve" : "decline" }
}

enum ProposalsWriterError: Error, Equatable {
    /// No section with the given heading line was found in the file.
    case proposalNotFound(headingLine: String)
    /// The section was found but had no `**Status:**` line to replace.
    case statusLineNotFound(headingLine: String)
    case readFailed(String)
    case writeFailed(String)
}

/// Serializes proposal status mutations to `dreaming-proposals.md`.
///
/// There is no `scoutctl` command for proposals — they are plain markdown that
/// dreaming sessions read and write directly — so the app edits the file in
/// place: locate the target section by its exact heading line, replace the
/// first `**Status:**` line within it, write the file atomically, then commit
/// just that file to the vault's git (matching how action-items writes and
/// dreaming runs commit every change). Submissions are strictly serialized so
/// two quick clicks can't interleave reads and writes of the same file.
actor ProposalsWriter {
    private let fileURL: URL
    private let scoutDirectory: URL
    private let gitService: GitServiceProtocol?
    private let now: @Sendable () -> Date

    /// Tail of the serial task chain (same pattern as `ActionItemsWriter`):
    /// each submission awaits the previous one before running.
    private var tail: Task<Void, Never>?

    init(
        fileURL: URL,
        scoutDirectory: URL,
        gitService: GitServiceProtocol?,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.fileURL = fileURL
        self.scoutDirectory = scoutDirectory
        self.gitService = gitService
        self.now = now
    }

    /// Apply a decision to the proposal identified by `headingLine`. Returns
    /// after the file is written and the git commit (best-effort) completes.
    func decide(_ decision: ProposalDecision, headingLine: String, code: String) async throws {
        let previous = tail
        let task = Task { [fileURL, scoutDirectory, gitService, now] in
            _ = await previous?.value
            return try await Self.perform(
                decision: decision,
                headingLine: headingLine,
                code: code,
                fileURL: fileURL,
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
        headingLine: String,
        code: String,
        fileURL: URL,
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
        let updated = try rewrite(text: text, headingLine: headingLine, newStatusValue: newStatusValue)

        // Nothing to do if the status is already exactly what we'd write.
        guard updated != text else { return }

        do {
            try updated.write(to: fileURL, atomically: true, encoding: .utf8)
        } catch {
            throw ProposalsWriterError.writeFailed(error.localizedDescription)
        }

        let label = code.isEmpty ? headingLine : code
        let relativePath = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        try? await gitService?.commitPaths(
            [relativePath],
            message: "app: \(decision.verb) proposal \(label)"
        )
    }

    // MARK: - Pure rewrite (unit-tested directly)

    /// Replace the `**Status:**` value of the section whose heading line equals
    /// `headingLine`. Only that one line changes — the body, code fences, and
    /// every other section are left byte-for-byte identical. Throws if the
    /// section or its status line cannot be found.
    static func rewrite(text: String, headingLine: String, newStatusValue: String) throws -> String {
        // Preserve the file's trailing-newline shape by splitting on "\n" and
        // rejoining; a trailing "" element round-trips a final newline.
        var lines = text.components(separatedBy: "\n")
        let wantedHeading = headingLine.trimmingCharacters(in: .whitespaces)

        guard let headingIndex = lines.firstIndex(where: {
            $0.trimmingCharacters(in: .whitespaces) == wantedHeading
        }) else {
            throw ProposalsWriterError.proposalNotFound(headingLine: headingLine)
        }

        // Scan the section body for the first `**Status:**` line.
        var k = headingIndex + 1
        while k < lines.count {
            let line = lines[k]
            if ProposalsParser.isProposalHeading(line) { break }
            if (line.hasPrefix("## ") || line.hasPrefix("# ")) && !line.hasPrefix("### ") { break }
            if ProposalsParser.statusValue(in: line) != nil {
                lines[k] = rebuildStatusLine(original: line, newValue: newStatusValue)
                return lines.joined(separator: "\n")
            }
            k += 1
        }
        throw ProposalsWriterError.statusLineNotFound(headingLine: headingLine)
    }

    /// Rebuild a status line, preserving the original leading indentation and
    /// the canonical `**Status:**` label, swapping only the value.
    private static func rebuildStatusLine(original: String, newValue: String) -> String {
        let leadingWhitespace = String(original.prefix(while: { $0 == " " || $0 == "\t" }))
        return "\(leadingWhitespace)**Status:** \(newValue)"
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
