import Foundation

/// Selector identifying which comment under a task to act on.
enum CommentSelector: Sendable, Equatable {
    /// 1-based index as `scoutctl --index` counts: user-authored sub-bullets
    /// and the `/scout-plan` markers (the `snoozed-until` marker is filtered).
    /// A card builds it with `ActionTask.commentSelector(at:)`.
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

    /// Edit-menu name: "Undo Mark Done".
    var actionName: String {
        switch self {
        case .addComment:    return "Add Comment"
        case .deleteComment: return "Delete Comment"
        case .editComment:   return "Edit Comment"
        case .markDone:      return "Mark Done"
        case .reopen:        return "Reopen"
        case .snooze:        return "Snooze"
        }
    }

    /// The engine's own inverse, when it has one: mark-done and reopen undo
    /// each other by `[#TAG]`, which stays correct even after a scheduled run
    /// rewrote other lines of the file. Every other op is undone by restoring
    /// the file's bytes.
    var inverse: WriteOp? {
        guard shortPrefix != nil else { return nil }
        switch self {
        case .markDone(let s, let p): return .reopen(subject: s, shortPrefix: p)
        case .reopen(let s, let p):   return .markDone(subject: s, shortPrefix: p)
        default: return nil
        }
    }

    /// The line change this op makes to the day file: one line inserted
    /// (snooze marker, new comment), changed (checkbox, edited comment) or
    /// removed (deleted comment). A before/after pair that differs by
    /// anything else also holds someone else's edit, made while scoutctl ran,
    /// so it isn't byte-undone. nil: no shape is known (multi-line text).
    var expectedLineDelta: LineDelta? {
        switch self {
        case .snooze:
            return LineDelta(removed: 0, added: 1)
        case .addComment(_, _, let text, _):
            return text.contains(where: \.isNewline) ? nil : LineDelta(removed: 0, added: 1)
        case .editComment(_, _, _, let text):
            return text.contains(where: \.isNewline) ? nil : LineDelta(removed: 1, added: 1)
        case .markDone, .reopen:
            return LineDelta(removed: 1, added: 1)
        case .deleteComment:
            return LineDelta(removed: 1, added: 0)
        }
    }

    /// Whether the changed lines are this op's own: the checkbox line holds
    /// the task's tag (or text), the inserted line is this snooze marker or
    /// this comment, the deleted line holds the selected text. Closes the
    /// cases a line count alone can't: a run editing a line at the same
    /// moment as an op that changed nothing, or as one of the same shape.
    func isOwnText(_ delta: LineDelta) -> Bool {
        func has(_ lines: [String], _ needle: String) -> Bool {
            lines.contains { $0.localizedCaseInsensitiveContains(needle) }
        }
        let task = shortPrefix.map { "[#\($0)]" } ?? subject
        switch self {
        case .markDone, .reopen:
            return has(delta.removedLines, task) && has(delta.addedLines, task)
        case .snooze(_, _, let until, _):
            return has(delta.addedLines, "snoozed-until: \(ActionItemsDay.stem(for: until))")
        case .addComment(_, _, let text, _):
            return has(delta.addedLines, text)
        case .editComment(_, _, _, let text):
            return has(delta.addedLines, text) && !has(delta.removedLines, text)
        case .deleteComment(_, _, let selector):
            if case .text(let text) = selector { return has(delta.removedLines, text) }
            return true
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
    /// - The author goes through `--author` (scoutctl 0.6.0+), which writes
    ///   `  - <author>: <text>`. It used to be embedded in the comment text,
    ///   which scoutctl then prefixed with its own default author, so every
    ///   comment from the app read `scout: <author>: <text>` and parsed as a
    ///   comment from Scout. The name is folded by `CommentAuthor.handle`
    ///   into the shape both parsers accept.
    /// - Reopen routes through `mark-done --undo`, which flips `[x]` back to
    ///   `[ ]` and refuses an ambiguous match rather than guessing.
    fileprivate func scoutctlArguments(dailyFilePath: URL) -> [String] {
        var args = ["action-items", scoutctlSubcommand, dailyFilePath.path]
        if let prefix = shortPrefix {
            args += ["--by-id", prefix]
        } else {
            args += ["--subject", subject]
        }
        switch self {
        case .addComment(_, _, let text, let author):
            args += ["--comment", text, "--author", CommentAuthor.handle(author)]
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
            args += ["--until", ActionItemsDay.stem(for: until)]
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
    /// A task with no `[#TAG]` is found by its text, so a day file that
    /// changed since the list was shown could hand the write a different
    /// line. Refused instead (C10).
    case stale(file: String)

    static func == (lhs: ActionItemsWriterError, rhs: ActionItemsWriterError) -> Bool {
        switch (lhs, rhs) {
        case let (.cliNonZeroExit(a, b, c), .cliNonZeroExit(d, e, f)):
            return a == d && b == e && c == f
        case (.processFailed, .processFailed): return true
        case let (.stale(a), .stale(b)): return a == b
        default: return false
        }
    }
}

struct WriteResult: Sendable {
    let stderr: String
    /// The day file's bytes before and after, for undo. nil when the op
    /// changed nothing, or the file couldn't be read back.
    var change: VaultChange? = nil
    /// Set when the write changed the file but has no undo, saying why.
    var undoUnavailable: UndoUnavailable? = nil
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
    ///
    /// `shownDigest` is the SHA-256 of the day file the list was parsed from.
    /// An op without a `[#TAG]` is refused with `.stale` when the file no
    /// longer hashes to it; a tagged op is resolved by scoutctl on the current
    /// bytes, so it needs no such check.
    @discardableResult
    func submit(_ op: WriteOp, displayedDate: Date, shownDigest: Data? = nil) async throws -> WriteResult {
        let previous = tail
        let task = Task { [scoutctl, argumentsPrefix, runner, actionItemsDirectory, scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.perform(
                op: op,
                displayedDate: displayedDate,
                shownDigest: shownDigest,
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

    /// Undo or redo a change this writer made, in the same serial queue as
    /// its writes, then commit the file.
    func restore(_ change: VaultChange, _ direction: UndoDirection) async throws {
        let previous = tail
        let task = Task { [gitService, scoutDirectory] in
            _ = await previous?.value
            try await change.apply(direction, git: gitService, repo: scoutDirectory)
        }
        tail = Task { _ = try? await task.value }
        try await task.value
    }

    private static func perform(
        op: WriteOp,
        displayedDate: Date,
        shownDigest: Data?,
        scoutctl: URL,
        argumentsPrefix: [String],
        actionItemsDirectory: URL,
        scoutDirectory: URL,
        runner: any ProcessRunner,
        gitService: GitService?
    ) async throws -> WriteResult {
        let dateISO = ActionItemsDay.stem(for: displayedDate)
        let dailyFile = actionItemsDirectory.appendingPathComponent("action-items-\(dateISO).md")

        // Stale check and undo baseline. A file we can't read here is left to
        // scoutctl to report; the write just won't be undoable.
        let before = try? FileSnapshot.capture(dailyFile)
        if op.shortPrefix == nil, let shownDigest, let before, before.digest != shownDigest {
            throw ActionItemsWriterError.stale(file: dailyFile.lastPathComponent)
        }

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
            // No retry on .noMatch: the list is stale, so the line a task was on
            // may now hold another task. The caller reparses and the user
            // acts on the fresh list.
            let cls = Self.classify(exitCode: result.exitCode, stderr: stderr)
            throw ActionItemsWriterError.cliNonZeroExit(
                exitCode: result.exitCode, stderr: stderr, classification: cls)
        }

        // Read "after" the moment scoutctl exits, before the commit, and keep
        // the change only if it is exactly this op's own line edit: a run that
        // wrote the day while scoutctl ran would otherwise be in the pair, and
        // undo would drop its edit along with ours.
        let after = try? FileSnapshot.capture(dailyFile)
        let (change, unavailable) = Self.undoCapture(op: op, before: before, after: after)

        let slug = Self.slugify(op.subject)
        let rel = Self.relativePathInRepo(fileURL: dailyFile, repo: scoutDirectory)
        try? await gitService?.commitPaths([rel], message: "action-items: \(op.verb) \(slug)")

        return WriteResult(stderr: stderr, change: change, undoUnavailable: unavailable)
    }

    /// The byte-undoable change for `op`, or why there is none. (nil, nil)
    /// when nothing changed.
    static func undoCapture(op: WriteOp, before: FileSnapshot?, after: FileSnapshot?)
        -> (change: VaultChange?, unavailable: UndoUnavailable?)
    {
        guard let before, let after else { return (nil, .unreadable) }
        guard let change = VaultChange(actionName: op.actionName, before: [before], after: [after]) else {
            return (nil, nil)
        }
        guard let expected = op.expectedLineDelta else { return (nil, .multiLineText) }
        let own = before.exists && change.files.allSatisfy { $0.lineDelta == expected && op.isOwnText($0.lineDelta) }
        return own ? (change, nil) : (nil, .mixedEdit)
    }

    /// Repo-relative path of `fileURL` under `repo`, falling back to the last
    /// path component if it isn't inside the repo. Mirrors the same helper in
    /// PerFileItemWriter so every writer commits scoped to the
    /// single file it touched rather than `git add -A` (issue #44).
    private static func relativePathInRepo(fileURL: URL, repo: URL) -> String {
        let full = fileURL.standardizedFileURL.path
        let prefix = repo.standardizedFileURL.path + "/"
        return full.hasPrefix(prefix) ? String(full.dropFirst(prefix.count)) : fileURL.lastPathComponent
    }

    /// Exit codes from `plugin/engine/scout/errors.py`: `ActionItemNotFound`
    /// exits 22 and `ActionItemAmbiguous` 23. Exit 2 is Click's usage error,
    /// which an engine older than the app prints for a flag it lacks.
    private static func classify(exitCode: Int32, stderr: String) -> ActionItemsWriterError.Classification {
        switch exitCode {
        case 22: return .noMatch
        case 23: return .ambiguous
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
