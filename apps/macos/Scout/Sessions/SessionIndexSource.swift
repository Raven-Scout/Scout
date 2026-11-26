import Foundation

/// The two builds `SessionIndexService` asks for (see `SessionsRefresh`).
nonisolated enum SessionIndexLane: Sendable, Equatable {
    /// `session index --json --no-gh`: liveness and state, PRs from the cache.
    case fast
    /// `session index --json`: also refreshes the PR cache through `gh`.
    case prs
}

/// Where the Sessions page gets its index. Production asks `scoutctl session
/// index`, which reads the Mac's real `~/.claude` and the desktop app's store
/// whatever vault the app points at. Tests, the big-vault fixture and a Debug
/// build pointed at a synthetic vault inject a `FileSessionIndexSource`
/// instead, so the Sessions page never reads a real person's sessions. (Other
/// readers of `~/.claude`, such as Action Items' Launch Claude folder lookup,
/// are configured separately: `AppState.Configuration.claudeSessionsDirectory`.)
///
/// The answer is a `ProcessResult`, so every source goes through the same
/// exit-code classification and decoding as the engine's output.
nonisolated protocol SessionIndexSource: Sendable {
    func build(_ lane: SessionIndexLane) async throws -> ProcessResult
}

/// `scoutctl session index`, the production source.
nonisolated struct ScoutctlSessionIndexSource: SessionIndexSource {
    let scoutctl: URL
    let argumentsPrefix: [String]
    let runner: any ProcessRunner
    let environment: [String: String]

    func build(_ lane: SessionIndexLane) async throws -> ProcessResult {
        let arguments = switch lane {
        case .fast: SessionsRefresh.fastArguments(prefix: argumentsPrefix)
        case .prs:  SessionsRefresh.prArguments(prefix: argumentsPrefix)
        }
        return try await runner.run(
            executable: scoutctl,
            arguments: arguments,
            environment: environment,
            workingDirectory: nil
        )
    }
}

/// A fixed `sessions-index.json`-shaped file, read on every build. Runs no
/// process and touches nothing outside `file`. Both lanes answer with the
/// file, so the PR line reads "refreshed" without any `gh` call.
nonisolated struct FileSessionIndexSource: SessionIndexSource {
    let file: URL

    /// A file that can't be read answers like a failed command (exit 1, the
    /// reason on stderr), so the page says "fixture file not found" instead of
    /// blaming a missing engine.
    func build(_ lane: SessionIndexLane) async throws -> ProcessResult {
        let url = file
        guard let data = await Task.detached(priority: .utility, operation: { try? Data(contentsOf: url) }).value else {
            return ProcessResult(exitCode: 1, stdout: Data(),
                                 stderr: Data("Sessions fixture file not found or unreadable: \(url.path)".utf8))
        }
        return ProcessResult(exitCode: 0, stdout: data, stderr: Data())
    }

    /// The `sessionsIndexFile` user default, if set: a Debug build reads its
    /// sessions from this file instead of the Mac's real Claude data (Release
    /// builds never call this; see `AppState.Configuration.production()`).
    /// Set it for a separate bundle ID only, e.g.
    /// `defaults write <dev bundle id> sessionsIndexFile /path/sessions-index.json`,
    /// or pass `-sessionsIndexFile /path/…` as a launch argument.
    static func configured(in defaults: UserDefaults) -> FileSessionIndexSource? {
        guard let raw = defaults.string(forKey: defaultsKey)?.trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty else { return nil }
        return FileSessionIndexSource(file: URL(fileURLWithPath: (raw as NSString).expandingTildeInPath))
    }

    static let defaultsKey = "sessionsIndexFile"
}
