import SwiftUI

/// Launch an action item in Claude Code or Claude Desktop. `.split` is the list
/// card's control: the primary button opens a new Claude Code session in
/// Claude Desktop and the chevron opens the full menu. `.icon` puts the same
/// menu behind one icon so it fits on a 280 pt board card.
struct LaunchClaudeMenu: View {
    enum Style { case split, icon }

    /// Built only when a launch happens, not on every render.
    let prompt: () -> String
    let scoutDirectory: URL
    var style: Style = .split
    /// The item's own Claude session (`- Session:`): a `claude.ai` link opens,
    /// a local session id resumes in the terminal.
    var session: String?
    /// Where the host shows a failed launch.
    @Binding var launchError: String?

    init(prompt: @escaping () -> String, scoutDirectory: URL, style: Style = .split, session: String? = nil, launchError: Binding<String?>) {
        self.prompt = prompt
        self.scoutDirectory = scoutDirectory
        self.style = style
        self.session = session
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

    var body: some View {
        switch style {
        case .split: split
        case .icon:  icon
        }
    }

    private var split: some View {
        HStack(spacing: 0) {
            Button {
                launch(.claudeDesktop(.code(folder: scoutDirectory)))
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 10))
                    Text("Launch Claude Code")
                        .font(DS.sans(11.5, weight: .medium))
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
                    .font(.system(size: 8))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.horizontal, 6)
                    .frame(height: 24)
                    .contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
        }
        .help("Launch this action item in Claude Code or Claude Desktop")
        .onKeyWindowHover { hovering in
            if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }

    private var icon: some View {
        Menu {
            menuItems
        } label: {
            Image(systemName: "sparkles")
                .font(.system(size: 11))
                .foregroundStyle(DS.Ink.p3)
                .frame(width: 24, height: 22)
                .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help("Launch this action item in Claude Code or Claude Desktop")
        .onKeyWindowHover { hovering in
            if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
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
            Label(cliMenuLabel, systemImage: "terminal")
        }
        Divider()
        Button {
            launch(.claudeDesktop(.chat))
        } label: {
            Label("Claude Desktop — new Chat", systemImage: "bubble.left.and.bubble.right")
        }
        Button {
            launch(.claudeDesktop(.cowork))
        } label: {
            Label("Claude Desktop — new Cowork task", systemImage: "person.2")
        }
        if let session, SessionReference(session).isActionable {
            Divider()
            sessionItem(session)
        }
    }

    @ViewBuilder
    private func sessionItem(_ session: String) -> some View {
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
        do {
            try ClaudeLauncher.resume(cliSessionID: id, cwd: folder, config: cliConfig)
            launchError = nil
        } catch {
            launchError = error.localizedDescription
        }
    }

    private var cliConfig: CLIConfig {
        CLIConfig(
            claudePathOverride: claudeCLIPath,
            terminal: CLITerminal(rawValue: cliTerminal) ?? .auto,
            customCommand: customLaunchCommand
        )
    }

    private var cliMenuLabel: String {
        switch CLITerminal(rawValue: cliTerminal) ?? .auto {
        case .auto:        return "Launch Claude Code (Auto)"
        case .terminalApp: return "Open in Terminal.app → Claude Code"
        case .iterm2:      return "Open in iTerm2 → Claude Code"
        case .custom:      return "Open in custom terminal → Claude Code"
        }
    }

    private func launch(_ target: ClaudeLauncher.Target) {
        do {
            try ClaudeLauncher.launch(target: target, prompt: prompt())
            launchError = nil
        } catch {
            launchError = error.localizedDescription
        }
    }
}
