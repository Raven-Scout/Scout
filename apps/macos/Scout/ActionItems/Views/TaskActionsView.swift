import SwiftUI

/// Editorial action row — flat buttons, thin hairline border on the primary
/// action, a muted chevron shortcut hint. Mirrors the handoff bundle's
/// `.act / .act.primary` language.
struct TaskActionsView: View {
    let task: ActionTask
    let kind: ActionSection.Kind
    let displayedDate: Date
    let scoutDirectory: URL
    /// The key Done binds, and shows as its keycap: ⌘↵ on the keyboard-current
    /// card while no comment editor has focus, else nil (UI audit #42).
    var doneShortcut: KeyboardShortcut? = nil
    let onOp: @MainActor (WriteOp) async -> Void

    @State private var showingSnooze = false
    @State private var launchError: String?
    @State private var didCopy = false
    @State private var copyResetTask: Task<Void, Never>?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Off while the engine check fails; Launch and Copy still work.
    @Environment(\.actionItemsWritesEnabled) private var writesEnabled

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                if task.done {
                    EditorialActionButton("Reopen", systemImage: "arrow.uturn.backward") {
                        Task { await onOp(.reopen(subject: task.matchableSubject, shortPrefix: task.shortPrefix)) }
                    }
                    .disabled(!writesEnabled)
                } else {
                    EditorialActionButton("Done", systemImage: "checkmark", style: .primary,
                                          keyboardShortcut: doneShortcut) {
                        Task { await onOp(.markDone(subject: task.matchableSubject, shortPrefix: task.shortPrefix)) }
                    }
                    .disabled(!writesEnabled)
                    EditorialActionButton("Snooze", systemImage: "moon.zzz") {
                        showingSnooze = true
                    }
                    .disabled(!writesEnabled)
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
                    LaunchClaudeMenu(task: task, scoutDirectory: scoutDirectory, launchError: $launchError)
                }
                copyMenu
            }
            if let launchError {
                Text(launchError)
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Status.err)
            }
        }
    }

    /// Split control mirroring `LaunchClaudeMenu`: the primary button copies
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
                        .font(DS.font(.caption2))
                        .contentTransition(reduceMotion ? .identity : .symbolEffect(.replace))
                    Text(didCopy ? "Copied" : "Copy")
                        .font(DS.font(.caption, weight: .medium))
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
                    .font(DS.font(.micro))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.horizontal, 6)
                    .frame(height: 24)
                    .contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton)
            .dsReportsOwnFocus()
            .accessibilityLabel("Copy format")
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Choose a copy format")
        }
        .keyWindowPointingHand()
    }

    /// Puts one action item on the clipboard in `format`. Also the card's
    /// context menu Copy.
    static func copy(_ task: ActionTask, format: ClaudeLauncher.CopyFormat, scoutDirectory: URL) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(
            ClaudeLauncher.prompt(
                for: task, format: format,
                contextNote: TaskContextNote.load(tag: task.shortPrefix, scoutDirectory: scoutDirectory)
            ),
            forType: .string
        )
    }

    private func copyTaskPrompt(format: ClaudeLauncher.CopyFormat) {
        Self.copy(task, format: format, scoutDirectory: scoutDirectory)
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
}
