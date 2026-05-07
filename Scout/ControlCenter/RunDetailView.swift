import SwiftUI
import AppKit

struct RunDetailView: View {
    let run: Run
    @EnvironmentObject var state: AppState
    @State private var confirmRetry = false
    @State private var resolvedCommits: [Commit] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            TabView {
                SummaryTab(logPath: run.logPath)
                    .tabItem { Label("Summary", systemImage: "text.alignleft") }
                LogViewer(logPath: run.logPath)
                    .tabItem { Label("Log", systemImage: "doc.text") }
                DiffViewer(commits: resolvedCommits)
                    .tabItem { Label("Diff", systemImage: "arrow.triangle.branch") }
                FilesTab(run: run)
                    .tabItem { Label("Files", systemImage: "folder") }
                ToolsTab(run: run)
                    .tabItem { Label("Tools", systemImage: "wrench.and.screwdriver") }
                ErrorsTab(errors: run.errorsDetected)
                    .tabItem { Label("Errors", systemImage: "exclamationmark.triangle") }
                FeedbackTab(run: run)
                    .tabItem { Label("Feedback", systemImage: "bubble.left.and.bubble.right") }
            }
            actionBar
        }
        .padding()
        .navigationTitle(run.displayName)
        .task(id: run.id) {
            // Resolve commits lazily on run selection to keep launch fast.
            resolvedCommits = await state.sessionLogService.commits(for: run)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(run.displayName).font(.title2).fontWeight(.bold)
                if run.wasManuallyTriggered && run.type != .manual {
                    Text("manual")
                        .font(.caption.monospaced())
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
                if let exit = run.exitCode {
                    Text("· Exit \(exit)")
                }
                if let cost = run.cost {
                    Text("· Cost $\(cost as NSDecimalNumber)")
                }
                if !run.commits.isEmpty || !resolvedCommits.isEmpty {
                    let n = max(run.commits.count, resolvedCommits.count)
                    Text("· \(n) commit\(n == 1 ? "" : "s")")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    private var statusPill: some View {
        HStack(spacing: 5) {
            Circle().fill(statusColor).frame(width: 6, height: 6)
            Text(run.status.rawValue)
                .font(.caption.monospaced())
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

    private var actionBar: some View {
        HStack(spacing: 8) {
            Button("Retry") { confirmRetry = true }
                .disabled(run.status == .running)
            Button("Open log file") {
                NSWorkspace.shared.open(run.logPath)
            }
            Button("Reveal in Finder") {
                NSWorkspace.shared.activateFileViewerSelecting([run.logPath])
            }
            Spacer()
        }
        .confirmationDialog(
            "Retry \(run.displayName)?",
            isPresented: $confirmRetry,
            titleVisibility: .visible
        ) {
            Button("Retry") {
                Task { await state.fireNow(slotKey: defaultSlotKey(for: run.type), bypassBudget: false) }
            }
            Button("Retry (bypass budget)") {
                Task { await state.fireNow(slotKey: defaultSlotKey(for: run.type), bypassBudget: true) }
            }
            Button("Cancel", role: .cancel) { }
        }
    }

    /// Map a `RunType` to a representative slot-key for retry.
    /// TODO(plan-6): Persist the originating slot-key on the `Run` record so
    /// retrying an `evening-consolidation` re-fires that slot specifically
    /// instead of the morning one. Plan 5 collapsed RunType to slot-type-aligned
    /// vocabulary, which made the consolidation/dreaming slot-key lossy at
    /// retry time. The defaults below pick one slot per family.
    private func defaultSlotKey(for type: RunType) -> String {
        switch type {
        case .morningBriefing:  return "morning-briefing"
        case .weekendBriefing:  return "weekend-briefing"
        case .consolidation:    return "morning-consolidation"
        case .dreaming:         return "dreaming-evening"
        case .research:         return "research"
        // .manual: route to the engine's manual slot type — schedule v2 has no
        // default manual slot, so this may resolve to a no-op until users add
        // one. Avoids silently re-firing as a briefing.
        case .manual:           return "manual"
        }
    }
}
