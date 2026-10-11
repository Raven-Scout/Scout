import AppKit

/// Delivers a note typed on the board to the item's own Claude session, the
/// way the reference board routes it: resume a local session, open a cloud
/// one, or start a new Claude Code session when the item has none. The full
/// prompt is always on the clipboard too, since only a new session can take
/// it pre-filled.
enum BoardPromptSender {
    enum Route: Equatable {
        case resume(id: String, folder: URL)
        case open(URL)
        case newSession
    }

    /// A local session resumes only when its transcript says which folder it
    /// ran in: `claude --resume` finds a session from that folder alone.
    /// Without it, the note starts a new session instead.
    static func route(
        for session: String?,
        folderFor: (String) -> URL? = { ClaudeSessionFolder.folder(forSessionID: $0) }
    ) -> Route {
        switch session.map(SessionReference.init) {
        case .local(let id): folderFor(id).map { .resume(id: id, folder: $0) } ?? .newSession
        case .cloud(let url): .open(url)
        case .unknown, nil: .newSession
        }
    }

    /// The note first, then what Claude needs to know about the item.
    static func prompt(note: String, context: String) -> String {
        "\(note.trimmingCharacters(in: .whitespacesAndNewlines))\n\n\(context)"
    }

    /// What the board shows once a note went out.
    static func confirmation(for route: Route) -> String {
        switch route {
        case .resume: "Resumed the item's session · your note is on the clipboard"
        case .open: "Opened the item's session · your note is on the clipboard"
        case .newSession: "Sent to Claude"
        }
    }

    @MainActor
    static func send(_ prompt: String, session: String?, scoutDirectory: URL, cli: CLIConfig) throws -> String {
        let route = route(for: session)
        switch route {
        case .resume(let id, let folder):
            copy(prompt)
            try ClaudeLauncher.resume(cliSessionID: id, cwd: folder, config: cli)
        case .open(let url):
            copy(prompt)
            NSWorkspace.shared.open(url)
        case .newSession:
            try startSession(prompt, in: scoutDirectory, cli: cli)
        }
        return confirmation(for: route)
    }

    /// A new Claude Code session in Claude Desktop, or in the terminal set in
    /// Settings when Claude Desktop isn't installed.
    @MainActor
    private static func startSession(_ prompt: String, in folder: URL, cli: CLIConfig) throws {
        do {
            try ClaudeLauncher.launch(target: .claudeDesktop(.code(folder: folder)), prompt: prompt)
        } catch ClaudeLauncher.LaunchError.claudeDesktopNotInstalled {
            try ClaudeLauncher.launch(target: .cli(cwd: folder, config: cli), prompt: prompt)
        }
    }

    @MainActor
    private static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}
