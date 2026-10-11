import Foundation

/// A write to the topic board's own files, outside any daily file.
enum BoardWriteOp: Sendable, Equatable {
    /// An empty `name` clears the rename.
    case renameTopic(tag: String, name: String)
    /// `key` is an Inbox or Loose ends entry's URL, or its text without one.
    case dismiss(key: String)

    var fileName: String {
        switch self {
        case .renameTopic: TopicsFileLoader.fileName
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
    private var supportsWrites: Bool?

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
    /// `scoutctl manifest show` advertises `topic_board_v1`. Asked once; an
    /// older engine gets the board without those two actions.
    func supportsBoardWrites() async -> Bool {
        if let cached = supportsWrites { return cached }
        let result = try? await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + ["manifest", "show"],
            environment: [:],
            workingDirectory: scoutDirectory
        )
        let supported = result.map { $0.exitCode == 0 && EngineFeatures.decode($0.stdout).contains(Self.feature) } ?? false
        supportsWrites = supported
        return supported
    }

    private func perform(_ op: BoardWriteOp) async throws {
        try await run(op.scoutctlArguments(dataDirectory: actionItemsDirectory.deletingLastPathComponent()))
        let file = actionItemsDirectory.appendingPathComponent(op.fileName)
        try? await git?.commitPaths([Self.relativePath(of: file, in: scoutDirectory)], message: op.commitMessage)
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

    static func relativePath(of file: URL, in repo: URL) -> String {
        let full = file.standardizedFileURL.path
        let prefix = repo.standardizedFileURL.path + "/"
        return full.hasPrefix(prefix) ? String(full.dropFirst(prefix.count)) : file.lastPathComponent
    }
}
