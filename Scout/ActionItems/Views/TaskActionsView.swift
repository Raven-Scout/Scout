import SwiftUI

/// Editorial action row — flat buttons, thin hairline border on the primary
/// action, a muted chevron shortcut hint. Mirrors the handoff bundle's
/// `.act / .act.primary` language.
struct TaskActionsView: View {
    let task: ActionTask
    let kind: ActionSection.Kind
    let displayedDate: Date
    let scoutDirectory: URL
    let onOp: @MainActor (WriteOp) async -> Void

    @AppStorage("claudeCLIPath")       private var claudeCLIPath: String = ""
    @AppStorage("cliTerminal")         private var cliTerminal: String = CLITerminal.auto.rawValue
    @AppStorage("customLaunchCommand") private var customLaunchCommand: String = ""

    @State private var showingSnooze = false
    @State private var launchError: String?
    @State private var didCopy = false
    @State private var copyResetTask: Task<Void, Never>?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                if task.done {
                    actButton("Reopen", systemImage: "arrow.uturn.backward", style: .plain) {
                        Task { await onOp(.reopen(subject: task.matchableSubject, shortPrefix: task.shortPrefix)) }
                    }
                } else {
                    actButton("Done", systemImage: "checkmark", style: .primary, shortcut: "⌘↵") {
                        Task { await onOp(.markDone(subject: task.matchableSubject, shortPrefix: task.shortPrefix)) }
                    }
                    actButton("Snooze", systemImage: "moon.zzz", style: .plain) {
                        showingSnooze = true
                    }
                    .popover(isPresented: $showingSnooze) {
                        SnoozePopoverView(sourceDate: displayedDate) { target in
                            await onOp(.snooze(
                                subject: task.matchableSubject,
                                shortPrefix: task.shortPrefix,
                                until: target,
                                fromKind: kind.rawValue
                            ))
                            showingSnooze = false
                        } onCancel: {
                            showingSnooze = false
                        }
                    }
                    launchClaudeMenu
                }
                copyMenu
            }
            if let launchError {
                Text(launchError)
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Status.err)
            }
        }
    }

    // MARK: - Launch Claude menu

    /// Split control: the primary button launches the default target (a new
    /// Claude Code session in Claude Desktop); the chevron opens the full menu.
    private var launchClaudeMenu: some View {
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

    /// Split control mirroring `launchClaudeMenu`: the primary button copies
    /// full context; the chevron is a real menu hit-region for the other
    /// formats. A `Menu(primaryAction:)` with a hidden indicator would make a
    /// click on the drawn chevron copy instead of opening the menu.
    private var copyMenu: some View {
        HStack(spacing: 0) {
            Button {
                copyTaskPrompt(format: .fullContext)
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: didCopy ? "checkmark" : "doc.on.doc")
                        .font(.system(size: 10))
                        .contentTransition(reduceMotion ? .identity : .symbolEffect(.replace))
                    Text(didCopy ? "Copied" : "Copy")
                        .font(DS.sans(11.5, weight: .medium))
                }
                .foregroundStyle(didCopy ? DS.Status.ok : DS.Ink.p3)
                .padding(.leading, 10)
                .padding(.trailing, 4)
                .frame(height: 24)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plainHit)
            .help("Copy full context to the clipboard")
            .accessibilityLabel(didCopy ? "Copied action-item context" : "Copy action-item context")

            Menu {
                ForEach(ClaudeLauncher.CopyFormat.allCases) { format in
                    Button {
                        copyTaskPrompt(format: format)
                    } label: {
                        Label(format.label, systemImage: format.systemImage)
                    }
                }
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
            .help("Choose a copy format")
        }
        .onHover { hovering in
            if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
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

    private func copyTaskPrompt(format: ClaudeLauncher.CopyFormat) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(
            ClaudeLauncher.prompt(for: task, format: format),
            forType: .string
        )
        didCopy = true
        // Cancel the previous reset so a rapid second copy keeps its
        // confirmation for the full 1.5 s instead of being snapped back
        // by the first click's sleeper.
        copyResetTask?.cancel()
        copyResetTask = Task { @MainActor in
            try? await Task.sleep(for: .seconds(1.5))
            guard !Task.isCancelled else { return }
            didCopy = false
        }
    }

    private enum ActStyle { case primary, plain }

    @ViewBuilder
    private func actButton(
        _ label: String,
        systemImage: String,
        style: ActStyle,
        shortcut: String? = nil,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: systemImage)
                    .font(.system(size: 10))
                Text(label)
                    .font(DS.sans(11.5, weight: .medium))
                if let shortcut {
                    Text(shortcut)
                        .font(DS.mono(10.5, weight: .medium))
                        .foregroundStyle(DS.Ink.p4)
                        .padding(.horizontal, 3)
                        .padding(.vertical, 1)
                        .overlay(RoundedRectangle(cornerRadius: 3).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                        .padding(.leading, 2)
                }
            }
            .foregroundStyle(style == .primary ? DS.Ink.p1 : DS.Ink.p3)
            .padding(.horizontal, 10)
            .frame(height: 24)
            .background {
                if style == .primary {
                    RoundedRectangle(cornerRadius: 5).fill(DS.Paper.raised)
                        .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(DS.Rule.hard, lineWidth: 0.5))
                }
            }
        }
        .buttonStyle(.plainHit)
        .help(label)
        .onHover { hovering in
            // Lightweight hover feedback via system cursor — no state churn.
            if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }
}
