import Foundation

enum WriteOp: Sendable {
    case addComment(subject: String, text: String, author: String)
    case markDone(subject: String)
    case reopen(subject: String)
    case snooze(subject: String, until: Date)

    var verb: String {
        switch self {
        case .addComment: return "comment"
        case .markDone:   return "mark-done"
        case .reopen:     return "reopen"
        case .snooze:     return "snooze"
        }
    }

    var subject: String {
        switch self {
        case .addComment(let s, _, _), .markDone(let s), .reopen(let s), .snooze(let s, _):
            return s
        }
    }

    /// scoutctl subcommand under `action-items`.
    fileprivate var scoutctlSubcommand: String {
        switch self {
        case .addComment:        return "add-comment"
        case .markDone, .reopen: return "mark-done"
        case .snooze:            return "snooze"
        }
    }

    /// Build the scoutctl arg list. `dailyFilePath` is the absolute path of
    /// the action-items markdown file we're writing into; scoutctl uses it
    /// as the positional `[PATH]` arg and decodes the date from the filename.
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
        args += ["--subject", subject]
        switch self {
        case .addComment(_, let text, let author):
            args += ["--comment", "\(author): \(text)"]
        case .reopen:
            args += ["--undo"]
        case .markDone:
            break
        case .snooze(_, let until):
            let fmt = DateFormatter()
            fmt.dateFormat = "yyyy-MM-dd"
            fmt.timeZone = TimeZone(identifier: "America/New_York")
            args += ["--until", fmt.string(from: until)]
        }
        return args
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
