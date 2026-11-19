import SwiftUI
import AppKit

enum RunDetailTab: String, CaseIterable, Identifiable, Hashable {
    case summary, log, diff, files, tools, errors, feedback
    var id: String { rawValue }
    var displayName: String {
        switch self {
        case .summary:  return "Summary"
        case .log:      return "Log"
        case .diff:     return "Diff"
        case .files:    return "Files"
        case .tools:    return "Tools"
        case .errors:   return "Errors"
        case .feedback: return "Feedback"
        }
    }
}

struct RunDetailView: View {
    let run: Run
    @EnvironmentObject var state: AppState
    @State private var confirmRetry = false
    /// The owner pressed Run again here, so a `fireNowError` is this view's to show.
    @State private var askedToRerun = false
    @State private var resolvedCommits: [Commit] = []
    /// The commits that put back the protected files this run changed (integrity `restored`).
    @State private var restoreCommits: [Commit] = []
    @SceneStorage("runDetailTab") private var tab: RunDetailTab = .summary

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            AdaptiveSegmentedControl(
                selection: $tab,
                options: RunDetailTab.allCases.map { ($0.displayName, $0) },
                minSegmentWidth: 70,
                accessibilityTitle: "Run detail tab"
            )
            tabContent
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            actionBar
        }
        .padding()
        .navigationTitle(run.displayName)
        .task(id: run.id) {
            // Resolve commits lazily on run selection to keep launch fast.
            resolvedCommits = await state.sessionLogService.commits(for: run)
            restoreCommits = await state.sessionLogService.restoreCommits(for: run)
        }
    }

    @ViewBuilder
    private var tabContent: some View {
        switch tab {
        case .summary:  SummaryTab(logPath: run.logPath)
        case .log:      LogViewer(logPath: run.logPath)
        case .diff:     DiffViewer(commits: restoreCommits + resolvedCommits.filter { c in !restoreCommits.contains(c) })
        case .files:    FilesTab(run: run)
        case .tools:    ToolsTab(run: run)
        case .errors:   ErrorsTab(errors: run.errorsDetected, runFailed: [.failure, .timeout, .orphaned].contains(run.status))
        case .feedback: FeedbackTab(run: run)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(run.displayName).font(DS.font(.title)).fontWeight(.bold)
                if run.wasManuallyTriggered && run.type != .manual {
                    Text("Started by you")
                        .font(DS.font(.caption2, .mono))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.secondary.opacity(0.15), in: RoundedRectangle(cornerRadius: 4))
                        .foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 14) {
                statusPill
                Text("Started \(run.startedAt.formatted(date: .abbreviated, time: .shortened))")
                if let end = run.endedAt {
                    Text("· Ended \(end.formatted(date: .omitted, time: .shortened))")
                }
                if let cost = run.cost {
                    Text("· Cost $\(cost as NSDecimalNumber)")
                }
                if !run.commits.isEmpty || !resolvedCommits.isEmpty {
                    let n = max(run.commits.count, resolvedCommits.count)
                    Text("· \(n) commit\(n == 1 ? "" : "s")")
                }
            }
            .font(DS.font(.caption2))
            .foregroundStyle(.secondary)
            if let problem = Problem(run: run) {
                ProblemView(problem: problem)
                    .padding(.top, 4)
            }
            if run.changedProtectedFile {
                ProtectedFileBlock(restored: run.protection?.integrity == "restored", commits: restoreCommits,
                                   showChange: { tab = .diff },
                                   openLog: { NSWorkspace.shared.open(run.logPath) })
                    .padding(.top, 4)
            }
            if run.ranUnprotected {
                Label(RunOutcomeDisplay.sandbox(run.protection?.sandbox, source: run.protection?.sandboxSource)
                      ?? "Write protection off", systemImage: "shield.slash")
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Status.warn)
                    .help("This run could change Scout's own files. Write protection is set in Settings ▸ Engine.")
            }
            if run.protectedFilesUnchecked {
                Label("Scout couldn't check this run's protected files afterwards", systemImage: "questionmark.diamond")
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Status.warn)
                    .help("The run's log has the integrity check's line saying why.")
            }
        }
    }

    private var statusPill: some View {
        HStack(spacing: 5) {
            Circle().fill(statusColor).frame(width: 6, height: 6)
            Text(run.status.displayName)
                .font(DS.font(.caption2))
                .foregroundStyle(statusColor)
        }
    }

    private var statusColor: Color {
        switch run.status {
        case .success: return .green
        case .failure, .timeout, .rateLimited: return .red
        case .running: return .orange
        default: return .secondary
        }
    }

    /// "Run again" fires this run's own schedule (`Run.rerunSlotKey`, from the
    /// run ledger), never one guessed from the run type: several schedules
    /// share a type, so a guess re-ran the morning consolidation for an
    /// evening one and couldn't re-run a schedule made in the app. There is no
    /// "ignore budget" variant: the engine's `fire-now` has no such option.
    private var actionBar: some View {
        let slot = run.rerunSlotKey
        let firing = slot.map { state.firingSlotKeys.contains($0) } ?? false
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Button(firing ? "Starting…" : "Run again") { confirmRetry = true }
                    .disabled(slot == nil || run.status == .running || firing)
                    .accessibilityHint(rerunUnavailableReason ?? "Runs \u{201C}\(scheduleName)\u{201D} now")
                Button("Open log") {
                    NSWorkspace.shared.open(run.logPath)
                }
                Button("Reveal in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([run.logPath])
                }
                Spacer()
            }
            if let reason = rerunUnavailableReason {
                Text(reason)
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p3)
                    .fixedSize(horizontal: false, vertical: true)
            } else if askedToRerun, let error = state.fireNowError {
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(DS.Status.warn)
                        .accessibilityHidden(true)
                    Text(error)
                        .font(DS.font(.caption))
                        .foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Couldn't run it again: \(error)")
            }
        }
        .onChange(of: run.id) { _, _ in askedToRerun = false }
        .confirmationDialog(
            "Run \u{201C}\(scheduleName)\u{201D} again?",
            isPresented: $confirmRetry,
            titleVisibility: .visible
        ) {
            if let slot {
                Button("Run again") {
                    askedToRerun = true
                    Task { await state.fireNow(slotKey: slot) }
                }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("Starts a new run now. The daily budget still applies.")
        }
    }

    /// The schedule's name, as the Schedules page shows it.
    private var scheduleName: String {
        run.rerunSlotKey.map(DisplayName.slotKey) ?? run.displayName
    }

    /// Why "Run again" is off, in the owner's words; nil when it's on.
    private var rerunUnavailableReason: String? {
        if run.status == .running { return nil }
        guard run.rerunSlotKey == nil else { return nil }
        if !run.startedBySchedule {
            return "An event trigger started this run, not a schedule, so there's none to run again."
        }
        if let key = run.slotKey, key == "manual" || key.hasSuffix("-manual") {
            return "This run wasn't started by a schedule, so there's none to run again. Use Run now on the Schedules page."
        }
        return "Scout doesn't know which schedule started this run. Use Run now on the Schedules page."
    }
}

/// The red block on a run whose post-run integrity check found a protected file
/// it had changed in git (card C9, from the run ledger's `integrity`): Scout put
/// it back in a new commit (`restored`), or couldn't (`unrestored`: a tag or the
/// stash still holds the change).
struct ProtectedFileBlock: View {
    let restored: Bool
    /// The restoring commits, when git has them; empty while loading or when not found.
    let commits: [Commit]
    let showChange: () -> Void
    let openLog: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.shield.fill")
                .foregroundStyle(DS.Status.err)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(DS.font(.body, weight: .semibold)).foregroundStyle(DS.Status.err)
                Text(message).font(DS.font(.label)).foregroundStyle(DS.Ink.p2)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                HStack(spacing: 12) {
                    if restored && !commits.isEmpty {
                        Button("Show the change", action: showChange)
                    }
                    Button("Open log", action: openLog)
                }
                .buttonStyle(.plainHit)
                .font(DS.font(.label))
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(DS.Status.err.opacity(0.08)))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Status.err.opacity(0.35), lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityLabel(title)
    }

    var title: String {
        restored ? "Scout put the protected file back" : "A protected file changed, and Scout couldn't put it back"
    }

    var message: String {
        if !restored {
            return "This run changed a file Scout protects, and a tag or the stash still holds the change. "
                + "The run's log names the file; put it back by hand."
        }
        guard let first = commits.first else {
            return "This run changed a file Scout protects, and the check after the run put it back in a new commit. "
                + "The run's log names the file and the commit."
        }
        let more = commits.count > 1 ? " and \(commits.count - 1) more" : ""
        return "This run changed a file Scout protects, and the check after the run put it back "
            + "in commit \(first.shortSHA)\(more)."
    }
}
