import AppKit

/// Delivers a note typed on the board to the item's own Claude session, the
/// way the reference board routes it: resume a local session, open a cloud
/// one, or start a new Claude Code session when the item has none. The full
/// prompt is always on the clipboard too, since only a new session can take
/// it pre-filled.
enum BoardPromptSender {
    enum Route: Equatable {
        case resume(id: String)
        case open(URL)
        case newSession
    }

    static func route(for session: String?) -> Route {
        switch session.map(SessionReference.init) {
        case .local(let id): .resume(id: id)
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
        case .resume(let id):
            copy(prompt)
            try ClaudeLauncher.resume(cliSessionID: id, cwd: scoutDirectory, config: cli)
        case .open(let url):
            copy(prompt)
            NSWorkspace.shared.open(url)
        case .newSession:
            try ClaudeLauncher.launch(target: .claudeDesktop(.code(folder: scoutDirectory)), prompt: prompt)
        }
        return confirmation(for: route)
    }

    @MainActor
    private static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}
