import SwiftUI

/// Launch an action item in Claude Code or Claude Desktop. `.split` is the list
/// card's control: the primary button opens a new Claude Code session in
/// Claude Desktop and the chevron opens the full menu. `.icon` puts the same
/// menu behind one icon so it fits on a 280 pt board card.
struct LaunchClaudeMenu: View {
    enum Style { case split, icon }

    /// Built only when a launch happens, not on every render. May fetch it (the
    /// engine builds "Resolve with Claude" prompts); a throw launches nothing and
    /// goes to `launchError`.
    let prompt: @MainActor () async throws -> String
    let scoutDirectory: URL
    var style: Style = .split
    /// The split button's label.
    var title: String = "Launch Claude Code"
    var helpText: String = "Launch this action item in Claude Code or Claude Desktop"
    /// The item's own Claude session (`- Session:`): a `claude.ai` link opens,
    /// a local session id resumes in the terminal.
    var session: String?
    /// Where the host shows a failed launch.
    @Binding var launchError: String?

    init(prompt: @escaping () -> String, scoutDirectory: URL, style: Style = .split, session: String? = nil, launchError: Binding<String?>) {
        self.prompt = { prompt() }
        self.scoutDirectory = scoutDirectory
        self.style = style
        self.session = session
        _launchError = launchError
    }

    /// A launcher whose prompt is fetched at click time, under its own title.
    init(title: String, help: String, asyncPrompt: @escaping @MainActor () async throws -> String,
         scoutDirectory: URL, launchError: Binding<String?>) {
        self.prompt = asyncPrompt
        self.scoutDirectory = scoutDirectory
        self.title = title
        self.helpText = help
        _launchError = launchError
    }

    init(task: ActionTask, scoutDirectory: URL, style: Style = .split, launchError: Binding<String?>) {
        self.init(
            prompt: {
                ClaudeLauncher.prompt(
                    for: task,
                    contextNote: TaskContextNote.load(tag: task.shortPrefix, scoutDirectory: scoutDirectory)
                )
            },
            scoutDirectory: scoutDirectory,
            style: style,
            launchError: launchError
        )
    }

    @AppStorage("claudeCLIPath")       private var claudeCLIPath: String = ""
    @AppStorage("cliTerminal")         private var cliTerminal: String = CLITerminal.auto.rawValue
    @AppStorage("customLaunchCommand") private var customLaunchCommand: String = ""
    /// A launch is finding `claude` or opening the terminal; a second click
    /// meanwhile would open a second one.
    @State private var isLaunching = false
    /// A launch macOS refused for a missing permission: shown as an alert
    /// with a button to the System Settings pane, not as inline text.
    @State private var permissionFailure: LaunchFailure?

    var body: some View {
        Group {
            switch style {
            case .split: split
            case .icon:  icon
            }
        }
        .launchFailureAlert("Couldn't open Claude", failure: $permissionFailure)
    }

    private var split: some View {
        HStack(spacing: 0) {
            Button {
                launch(.claudeDesktop(.code(folder: scoutDirectory)))
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: "sparkles")
                        .font(DS.font(.caption2))
                    Text(title)
                        .font(DS.font(.caption, weight: .medium))
                }
                .foregroundStyle(DS.Ink.p3)
                .padding(.leading, 10)
                .padding(.trailing, 4)
                .frame(height: 24)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plainHit)

            Menu {
                menuItems
            } label: {
                Image(systemName: "chevron.down")
                    .font(DS.font(.micro))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.horizontal, 6)
                    .frame(height: 24)
                    .contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .accessibilityLabel("More ways to launch in Claude")
        }
        .disabled(isLaunching)
        .help(helpText)
        .keyWindowPointingHand()
    }

    private var icon: some View {
        Menu {
            menuItems
        } label: {
            Image(systemName: "sparkles")
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p3)
                .frame(width: 24, height: 22)
                .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help(helpText)
        .accessibilityLabel("Launch in Claude")
        .keyWindowPointingHand()
    }

    @ViewBuilder
    private var menuItems: some View {
        Button {
            launch(.claudeDesktop(.code(folder: scoutDirectory)))
        } label: {
            Label("Claude Desktop — new Claude Code session",
                  systemImage: "chevron.left.forwardslash.chevron.right")
        }
        Divider()
        Button {
            launch(.cli(cwd: scoutDirectory, config: cliConfig))
        } label: {
            Label(Self.cliMenuLabel(for: CLITerminal(rawValue: cliTerminal) ?? .auto), systemImage: "terminal")
        }
        Divider()
        Button {
            launch(.claudeDesktop(.chat))
        } label: {
            Label("Claude Desktop — new Chat", systemImage: "bubble.left.and.bubble.right")
        }
        Button {
            launch(.claudeDesktop(.cowork(folder: scoutDirectory)))
        } label: {
            Label("Claude Desktop — new Cowork task", systemImage: "person.2")
        }
        if let session, SessionReference(session).isActionable {
            Divider()
            sessionItem(session)
        }
    }

    @ViewBuilder
    private func sessionItem(_ session: String) -> some View { // ds-guard: menu items
        switch SessionReference(session) {
        case .cloud(let url):
            Button {
                NSWorkspace.shared.open(url)
            } label: {
                Label("Open this item's Claude session", systemImage: "arrow.up.forward.app")
            }
        case .local(let id):
            Button {
                resume(id)
            } label: {
                Label("Resume this item's Claude session", systemImage: "arrow.uturn.forward")
            }
        case .unknown:
            EmptyView()
        }
    }

    /// `claude --resume` finds a session only from the folder it ran in, read
    /// from its transcript. Without a transcript on this Mac, a new session
    /// starts instead.
    private func resume(_ id: String) {
        guard let folder = ClaudeSessionFolder.folder(forSessionID: id) else {
            launch(.claudeDesktop(.code(folder: scoutDirectory)))
            return
        }
        let config = cliConfig
        run { try await ClaudeLauncher.resume(cliSessionID: id, cwd: folder, config: config) }
    }

    private var cliConfig: CLIConfig {
        CLIConfig(
            claudePathOverride: claudeCLIPath,
            terminal: CLITerminal(rawValue: cliTerminal) ?? .auto,
            customCommand: customLaunchCommand
        )
    }

    /// The terminal item's label. Only the split button says "Launch Claude
    /// Code" (it opens Claude Desktop); this item opens a terminal, so it
    /// names the terminal like its siblings (audit #51: two items had the
    /// same name and opened different apps).
    static func cliMenuLabel(for terminal: CLITerminal) -> String {
        switch terminal {
        case .auto:        return "Open in a terminal → Claude Code"
        case .terminalApp: return "Open in Terminal.app → Claude Code"
        case .iterm2:      return "Open in iTerm2 → Claude Code"
        case .custom:      return "Open in custom terminal → Claude Code"
        }
    }

    /// The prompt is built (or fetched) inside `run`, so `isLaunching` covers it.
    private func launch(_ target: ClaudeLauncher.Target) {
        guard !isLaunching else { return }
        let make = prompt
        run {
            let text = try await make()
            try await ClaudeLauncher.launch(target: target, prompt: text)
        }
    }

    /// What the host's inline text says for a launch that failed for a reason other
    /// than a missing permission (a prompt the engine couldn't build, among others).
    static func launchErrorText(for error: Error) -> String {
        LaunchFailure(error).message
    }

    /// One launch at a time. A permission refusal gets the alert with its
    /// System Settings button; anything else goes to the host's inline text.
    private func run(_ body: @escaping @MainActor () async throws -> Void) {
        guard !isLaunching else { return }
        isLaunching = true
        Task {
            defer { isLaunching = false }
            do {
                try await body()
                launchError = nil
            } catch {
                let failure = LaunchFailure(error)
                if failure.settingsURL != nil {
                    launchError = nil
                    permissionFailure = failure
                } else {
                    launchError = Self.launchErrorText(for: error)
                }
            }
        }
    }
}
