import Foundation

/// Selector identifying which comment under a task to act on.
enum CommentSelector: Sendable, Equatable {
    /// 1-based index, counting only user-authored sub-bullets (the
    /// `snoozed-until` marker is filtered).
    case index(Int)
    /// Case-insensitive substring of the comment body.
    case text(String)
}

enum WriteOp: Sendable {
    case addComment(subject: String, shortPrefix: String?, text: String, author: String)
    case deleteComment(subject: String, shortPrefix: String?, selector: CommentSelector)
    case editComment(subject: String, shortPrefix: String?, selector: CommentSelector, newText: String)
    case markDone(subject: String, shortPrefix: String?)
    case reopen(subject: String, shortPrefix: String?)
    /// `fromKind` is the source section's `ActionSection.Kind.rawValue` (e.g.
    /// "urgent", "todo"). Passed to scoutctl as `--from-kind` so the marker
    /// remembers the original priority. ``nil`` for legacy callers.
    case snooze(subject: String, shortPrefix: String?, until: Date, fromKind: String?)

    var verb: String {
        switch self {
        case .addComment:    return "comment"
        case .deleteComment: return "delete-comment"
        case .editComment:   return "edit-comment"
        case .markDone:      return "mark-done"
        case .reopen:        return "reopen"
        case .snooze:        return "snooze"
        }
    }

    var subject: String {
        switch self {
        case .addComment(let s, _, _, _),
             .deleteComment(let s, _, _),
             .editComment(let s, _, _, _),
             .markDone(let s, _),
             .reopen(let s, _),
             .snooze(let s, _, _, _):
            return s
        }
    }

    var shortPrefix: String? {
        switch self {
        case .addComment(_, let p, _, _),
             .deleteComment(_, let p, _),
             .editComment(_, let p, _, _),
             .markDone(_, let p),
             .reopen(_, let p),
             .snooze(_, let p, _, _):
            return p
        }
    }

    /// scoutctl subcommand under `action-items`.
    fileprivate var scoutctlSubcommand: String {
        switch self {
        case .addComment:        return "add-comment"
        case .deleteComment:     return "delete-comment"
        case .editComment:       return "edit-comment"
        case .markDone, .reopen: return "mark-done"
        case .snooze:            return "snooze"
        }
    }

    /// Build the scoutctl arg list. `dailyFilePath` is the absolute path of
    /// the action-items markdown file we're writing into; scoutctl uses it
    /// as the positional `[PATH]` arg and decodes the date from the filename.
    ///
    /// **Target selection:** when `shortPrefix` is non-nil, pass `--by-id`
    /// for a structural ID match — bypasses the brittle markdown-substring
    /// path entirely. Fall back to `--subject` only for legacy unprefixed
    /// lines (carryovers from before the v0.5.5 prefix mandate landed).
    ///
    /// Notes vs the pre-v0.5.2 legacy-script invocation:
    /// - We embed the author inline in the comment body (`<author>: <text>`)
    ///   because scoutctl's `add-comment` doesn't accept an `--author` flag.
    ///   ActionItemsParser already tolerates this format.
    /// - Reopen routes through `mark-done --undo`. scoutctl doesn't currently
    ///   expose `--undo` (BACKLOG: add scoutctl mark-done --undo); the call
    ///   will fail with a clear "no such option" error until the plugin
    ///   catches up.
    fileprivate func scoutctlArguments(dailyFilePath: URL) -> [String] {
        var args = ["action-items", scoutctlSubcommand, dailyFilePath.path]
        if let prefix = shortPrefix {
            args += ["--by-id", prefix]
        } else {
            args += ["--subject", subject]
        }
        switch self {
        case .addComment(_, _, let text, let author):
            args += ["--comment", "\(author): \(text)"]
        case .deleteComment(_, _, let selector):
            args += Self.selectorArguments(selector)
        case .editComment(_, _, let selector, let newText):
            args += Self.selectorArguments(selector)
            args += ["--new-text", newText]
        case .reopen:
            args += ["--undo"]
        case .markDone:
            break
        case .snooze(_, _, let until, let fromKind):
            let fmt = DateFormatter()
            fmt.dateFormat = "yyyy-MM-dd"
            fmt.timeZone = TimeZone(identifier: "America/New_York")
            args += ["--until", fmt.string(from: until)]
            if let fromKind, !fromKind.isEmpty {
                args += ["--from-kind", fromKind]
            }
        }
        return args
    }

    private static func selectorArguments(_ s: CommentSelector) -> [String] {
        switch s {
        case .index(let n):   return ["--index", String(n)]
        case .text(let body): return ["--text", body]
        }
    }
}

enum ActionItemsWriterError: Error, Equatable {
    enum Classification: Equatable { case noMatch, ambiguous, environment, other }
    case cliNonZeroExit(exitCode: Int32, stderr: String, classification: Classification)
    case processFailed(Error)

    static func == (lhs: ActionItemsWriterError, rhs: ActionItemsWriterError) -> Bool {
        switch (lhs, rhs) {
        case let (.cliNonZeroExit(a, b, c), .cliNonZeroExit(d, e, f)):
            return a == d && b == e && c == f
        case (.processFailed, .processFailed): return true
        default: return false
        }
    }
}

struct WriteResult: Sendable {
    let stderr: String
}

/// Serializes action-item mutations through `scoutctl action-items <op>`.
///
/// v0.5.2 rewrite: previously shelled out to standalone Python scripts at
/// `~/Scout/action-items/{add_comment,mark_done,snooze}.py`. Those scripts
/// are legacy — modern scout-plugin installs ship the same logic only via
/// the `scoutctl action-items` subcommands. Friend-install bug surfaced
/// the dependency; now the app only needs scoutctl on disk.
///
/// v0.5.5: prefers `--by-id <prefix>` over `--subject` when a task carries
/// a `[#XXXX]` short prefix. The plugin mandates prefixes on every new line
/// (scout-plugin PR #28), so subject-matching becomes the fallback for
/// legacy unprefixed carryovers only.
actor ActionItemsWriter {
    private let scoutctl: URL
    private let argumentsPrefix: [String]
    private let actionItemsDirectory: URL
    private let scoutDirectory: URL
    private let runner: any ProcessRunner
    private let gitService: GitService?

    /// Tail of the serial task chain. Each new submission awaits the previous
    /// task's completion before running, giving us a true serial queue on top
    /// of the actor (actors are re-entrant at each `await`, so a plain actor
    /// method isn't enough to serialize across suspensions).
    private var tail: Task<Void, Never>?

    init(
        scoutctl: URL,
        argumentsPrefix: [String] = [],
        actionItemsDirectory: URL,
        scoutDirectory: URL,
        runner: any ProcessRunner,
        gitService: GitService?
    ) {
        self.scoutctl = scoutctl
        self.argumentsPrefix = argumentsPrefix
        self.actionItemsDirectory = actionItemsDirectory
        self.scoutDirectory = scoutDirectory
        self.runner = runner
        self.gitService = gitService
    }

    /// Submit a write. Submissions are strictly serialized — only one CLI
    /// runs end-to-end at a time, even across concurrent callers.
    @discardableResult
    func submit(_ op: WriteOp, displayedDate: Date) async throws -> WriteResult {
        let previous = tail
        let task = Task { [scoutctl, argumentsPrefix, runner, actionItemsDirectory, scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.perform(
                op: op,
                displayedDate: displayedDate,
                scoutctl: scoutctl,
                argumentsPrefix: argumentsPrefix,
                actionItemsDirectory: actionItemsDirectory,
                scoutDirectory: scoutDirectory,
                runner: runner,
                gitService: gitService
            )
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    private static func perform(
        op: WriteOp,
        displayedDate: Date,
        scoutctl: URL,
        argumentsPrefix: [String],
        actionItemsDirectory: URL,
        scoutDirectory: URL,
        runner: any ProcessRunner,
        gitService: GitService?
    ) async throws -> WriteResult {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"
        fmt.timeZone = TimeZone(identifier: "America/New_York")
        let dateISO = fmt.string(from: displayedDate)
        let dailyFile = actionItemsDirectory.appendingPathComponent("action-items-\(dateISO).md")

        let result: ProcessResult
        do {
            result = try await runner.run(
                executable: scoutctl,
                arguments: argumentsPrefix + op.scoutctlArguments(dailyFilePath: dailyFile),
                environment: [:],
                workingDirectory: scoutDirectory
            )
        } catch {
            throw ActionItemsWriterError.processFailed(error)
        }

        let stderr = String(data: result.stderr, encoding: .utf8) ?? ""
        if result.exitCode != 0 {
            throw ActionItemsWriterError.cliNonZeroExit(
                exitCode: result.exitCode,
                stderr: stderr,
                classification: Self.classify(exitCode: result.exitCode, stderr: stderr)
            )
        }

        let slug = Self.slugify(op.subject)
        try? await gitService?.commitAll(message: "action-items: \(op.verb) \(slug)")

        return WriteResult(stderr: stderr)
    }

    private static func classify(exitCode: Int32, stderr: String) -> ActionItemsWriterError.Classification {
        switch exitCode {
        case 2: return .noMatch
        case 3: return .ambiguous
        case 1, 4, 5: return .other
        default:
            let s = stderr.lowercased()
            if s.contains("no such option") || s.contains("no module named") || s.contains("command not found") {
                return .environment
            }
            return .other
        }
    }

    private static func slugify(_ s: String) -> String {
        let trimmed = s.trimmingCharacters(in: .whitespacesAndNewlines)
        let prefix = trimmed.prefix(40)
        return prefix
            .replacingOccurrences(of: " ", with: "-")
            .replacingOccurrences(of: "\"", with: "")
    }
}
