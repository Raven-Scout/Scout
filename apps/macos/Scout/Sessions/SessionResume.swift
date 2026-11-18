import Foundation

/// "Resume" on the Sessions page: find the folder the session ran in, then
/// run `claude --resume` there, with nothing on the main thread that can
/// block it (UI audit #32, #36).
enum SessionResume {
    /// Why a resume didn't start, in words the alert shows as they are.
    nonisolated enum Failure: LocalizedError, Equatable {
        /// The index has no CLI session id for it (the button is disabled
        /// then; this guards a stale selection).
        case noCLISession
        /// Neither the session's own folder nor its project folder exists.
        case folderMissing(String)

        var errorDescription: String? {
            switch self {
            case .noCLISession:
                return "Claude Code didn't record a session id for this one, so it can't be resumed from a terminal."
            case .folderMissing(let path):
                return "The folder this session ran in is gone (\(path)), so Claude can't find the conversation to resume it. If the folder was moved, open Claude Code there and use /resume."
            }
        }
    }

    /// Launches `claude --resume <id>` in `folder` with `config`.
    typealias Launch = @MainActor (_ cliSessionID: String, _ folder: URL, _ config: CLIConfig) async throws -> Void

    /// Resolves the folder off the main thread, then launches. Returns a note
    /// for the page when the resume started somewhere it may not work (the
    /// project folder, because the session's own folder is gone); nil when
    /// it started in the session's own folder. `launch` is injected so tests
    /// never start a terminal.
    @MainActor
    static func run(
        _ session: AgentSession,
        config: CLIConfig,
        claudeHome: URL? = ClaudeSessionFolder.productionClaudeHome,
        launch: Launch = { try await ClaudeLauncher.resume(cliSessionID: $0, cwd: $1, config: $2) }
    ) async throws -> String? {
        guard let id = session.cliSessionID, !id.isEmpty else { throw Failure.noCLISession }
        let found = await Task.detached(priority: .userInitiated) {
            SessionsFormat.resumeFolder(for: session, claudeHome: claudeHome)
        }.value
        guard let folder = found else {
            throw Failure.folderMissing(session.cwd.isEmpty ? session.originCwd : session.cwd)
        }
        try await launch(id, folder.url, config)
        return note(for: folder)
    }

    static func note(for folder: ResumeFolder) -> String? {
        switch folder {
        case .own:
            return nil
        case .project(let url):
            return "This session's own folder is gone, so it opened from the project folder (\(url.lastPathComponent)). If Claude says it can't find the conversation, it can't be resumed."
        }
    }
}
