import Foundation
import os

/// A write to the topic board's own files, outside any daily file.
enum BoardWriteOp: Sendable, Equatable {
    /// An empty `name` clears the rename.
    case renameTopic(tag: String, name: String)
    /// `key` is an Inbox or Loose ends entry's URL, or its text without one.
    case dismiss(key: String)

    var fileName: String {
        switch self {
        case .renameTopic: TopicsFileLoader.namesFileName
        case .dismiss: DismissedFileLoader.fileName
        }
    }

    func scoutctlArguments(dataDirectory: URL) -> [String] {
        switch self {
        case .renameTopic(let tag, let name):
            ["action-items", "rename-topic", tag, "--name", name, "--data-dir", dataDirectory.path]
        case .dismiss(let key):
            ["action-items", "dismiss", "--key", key, "--data-dir", dataDirectory.path]
        }
    }

    /// What the board shows when the write fails: scoutctl's own reason when
    /// it gave one, so "no such topic" and a broken engine read differently.
    func failureMessage(for error: Error) -> String {
        let action = switch self {
        case .renameTopic: "Couldn't save the new name"
        case .dismiss: "Couldn't close that entry"
        }
        guard let reason = Self.reason(from: error) else { return "\(action). Try again." }
        return "\(action): \(reason)"
    }

    private static func reason(from error: Error) -> String? {
        guard case ActionItemsWriterError.cliNonZeroExit(_, let stderr, _) = error else { return nil }
        let line = stderr.split(whereSeparator: \.isNewline).last { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        guard let line else { return nil }
        let text = line.trimmingCharacters(in: .whitespaces)
        for prefix in ["Error: ", "error: "] where text.hasPrefix(prefix) {
            return String(text.dropFirst(prefix.count))
        }
        return text
    }

    var commitMessage: String {
        switch self {
        case .renameTopic(let tag, let name): name.isEmpty ? "action-items: clear topic name #\(tag)" : "action-items: rename topic #\(tag)"
        case .dismiss: "action-items: dismiss entry"
        }
    }
}

/// Runs `BoardWriteOp`s through `scoutctl` one at a time and commits the one
/// file each touched.
actor BoardWriter {
    private let scoutctl: URL
    private let argumentsPrefix: [String]
    private let actionItemsDirectory: URL
    private let scoutDirectory: URL
    private let runner: any ProcessRunner
    private let git: (any GitServiceProtocol)?
    private var tail: Task<Void, Never>?
    private var supportsWrites = false
    private static let log = Logger(subsystem: "com.scout.Scout", category: "BoardWriter")

    static let feature = "topic_board_v1"

    init(
        scoutctl: URL,
        argumentsPrefix: [String] = [],
        actionItemsDirectory: URL,
        scoutDirectory: URL,
        runner: any ProcessRunner,
        git: (any GitServiceProtocol)?
    ) {
        self.scoutctl = scoutctl
        self.argumentsPrefix = argumentsPrefix
        self.actionItemsDirectory = actionItemsDirectory
        self.scoutDirectory = scoutDirectory
        self.runner = runner
        self.git = git
    }

    func submit(_ op: BoardWriteOp) async throws {
        let previous = tail
        let task = Task { [self] in
            _ = await previous?.value
            try await perform(op)
        }
        tail = Task { _ = try? await task.value }
        try await task.value
    }

    /// Whether the installed engine has `rename-topic` and `dismiss`: its
    /// `scoutctl manifest show` advertises `topic_board_v1`. Only a yes is
    /// remembered: an older engine, or a probe that failed at launch, is asked
    /// again next time, so an engine upgraded while the app runs shows them.
    func supportsBoardWrites() async -> Bool {
        if supportsWrites { return true }
        let result = try? await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + ["manifest", "show"],
            environment: [:],
            workingDirectory: scoutDirectory
        )
        supportsWrites = result.map { $0.exitCode == 0 && EngineFeatures.decode($0.stdout).contains(Self.feature) } ?? false
        return supportsWrites
    }

    private func perform(_ op: BoardWriteOp) async throws {
        try await run(op.scoutctlArguments(dataDirectory: actionItemsDirectory.deletingLastPathComponent()))
        let file = actionItemsDirectory.appendingPathComponent(op.fileName)
        guard let path = Self.relativePath(of: file, in: scoutDirectory) else {
            Self.log.debug("Not committing \(file.path, privacy: .public): outside \(self.scoutDirectory.path, privacy: .public)")
            return
        }
        do {
            try await git?.commitPaths([path], message: op.commitMessage)
        } catch {
            Self.log.debug("Commit of \(path, privacy: .public) failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    private func run(_ arguments: [String]) async throws {
        let result: ProcessResult
        do {
            result = try await runner.run(
                executable: scoutctl,
                arguments: argumentsPrefix + arguments,
                environment: [:],
                workingDirectory: scoutDirectory
            )
        } catch {
            throw ActionItemsWriterError.processFailed(error)
        }
        guard result.exitCode == 0 else {
            throw ActionItemsWriterError.cliNonZeroExit(
                exitCode: result.exitCode,
                stderr: String(data: result.stderr, encoding: .utf8) ?? "",
                classification: .other
            )
        }
    }

    /// `file`'s path inside `repo`, or `nil` when it lies outside it.
    static func relativePath(of file: URL, in repo: URL) -> String? {
        let full = file.standardizedFileURL.path
        let prefix = repo.standardizedFileURL.path + "/"
        return full.hasPrefix(prefix) ? String(full.dropFirst(prefix.count)) : nil
    }
}
