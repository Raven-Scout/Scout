import SwiftUI

/// Editorial action row — flat buttons, thin hairline border on the primary
/// action, a muted chevron shortcut hint. Mirrors the handoff bundle's
/// `.act / .act.primary` language.
struct TaskActionsView: View {
    let task: ActionTask
    let kind: ActionSection.Kind
    let displayedDate: Date
    let scoutDirectory: URL
    let onOp: (WriteOp) async -> Void

    @State private var showingSnooze = false
    @State private var launchError: String?

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
            }
            if let launchError {
                Text(launchError)
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Status.err)
            }
        }
    }

    // MARK: - Launch Claude menu

    private var launchClaudeMenu: some View {
        Menu {
            Button {
                launch(.ghostty(cwd: scoutDirectory))
            } label: {
                Label("Ghostty → tmux + Claude Code", systemImage: "terminal")
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
            HStack(spacing: 5) {
                Image(systemName: "sparkles")
                    .font(.system(size: 10))
                Text("Launch Claude")
                    .font(DS.sans(11.5, weight: .medium))
                Image(systemName: "chevron.down")
                    .font(.system(size: 8))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.leading, 1)
            }
            .foregroundStyle(DS.Ink.p3)
            .padding(.horizontal, 10)
            .frame(height: 24)
            .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .onHover { hovering in
            if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
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
        .onHover { hovering in
            // Lightweight hover feedback via system cursor — no state churn.
            if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }

    private func launchClaude() {
        let escaped = task.plainSubject
            .replacingOccurrences(of: #"\"#, with: #"\\"#)
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "$", with: "\\$")
        GhosttyLauncher.openNewTab(
            cwd: scoutDirectory,
            runningCommand: "claude \"Help me make progress on this action item: \(escaped)\""
        )
    }
}
