import SwiftUI

/// Launch an action item in Claude Code or Claude Desktop. `.split` is the list
/// card's control: the primary button opens a new Claude Code session in
/// Claude Desktop and the chevron opens the full menu. `.icon` puts the same
/// menu behind one icon so it fits on a 280 pt board card.
struct LaunchClaudeMenu: View {
    enum Style { case split, icon }

    let task: ActionTask
    let scoutDirectory: URL
    var style: Style = .split
    /// Where the host shows a failed launch.
    @Binding var launchError: String?

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
        .onHover { hovering in
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
        .onHover { hovering in
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
            let config = CLIConfig(
                claudePathOverride: claudeCLIPath,
                terminal: CLITerminal(rawValue: cliTerminal) ?? .auto,
                customCommand: customLaunchCommand
            )
            launch(.cli(cwd: scoutDirectory, config: config))
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
            try ClaudeLauncher.launch(target: target, prompt: ClaudeLauncher.prompt(for: task))
            launchError = nil
        } catch {
            launchError = error.localizedDescription
        }
    }
}
