import Foundation
import SwiftUI
import Combine

@MainActor
final class AppState: ObservableObject {
    enum MenuBarStatus { case idle, running, lastFailed, budgetSkipped }

    @Published var menuBarStatus: MenuBarStatus = .idle

    // Existing Control Center services
    let fileWatcher: FileWatcher
    let trackerService: UsageTrackerService
    let sessionTokensService: SessionTokensService
    let connectorHealthService: ConnectorHealthService
    let sessionLogService: SessionLogService
    let scheduleService: ScheduleService
    let powerStateService: PowerStateService
    let scheduleEditorService: ScheduleEditorService
    let gitService: GitService
    let notificationService: NotificationService
    let claudeSessionService: ClaudeSessionService

    // Process runner kept at app level so fire-now shell-outs (UpcomingStripView,
    // RunDetailView, MenuBarExtraContent) can invoke `scoutctl schedule fire-now`
    // without each consumer constructing its own runner.
    let runner: any ProcessRunner
    let scoutctlExecutable: URL

    // New Action Items services
    let actionItemsDocumentService: ActionItemsDocumentService
    let actionItemsWriterBox: ActionItemsWriterBox
    let actionItemsEnvState: ActionItemsEnvironmentState
    let scoutDirectory: URL
    let actionItemsDirectory: URL

    private var previousStatus: [Run.ID: RunStatus] = [:]
    private var cancellables: Set<AnyCancellable> = []

    init() {
        let scoutDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Scout")
        let actionItemsDir = scoutDir.appendingPathComponent("action-items")
        let watcher = FileWatcher()
        let runner = SystemProcessRunner()

        let git = GitService(repoURL: scoutDir, runner: runner)
        let tracker = UsageTrackerService(
            trackerURL: scoutDir.appendingPathComponent(".scout-logs/usage-tracker.jsonl"),
            fileEvents: watcher
        )
        let tokens = SessionTokensService(
            trackerURL: scoutDir.appendingPathComponent(".scout-logs/session-tokens.jsonl"),
            fileEvents: watcher
        )
        let connectorHealth = ConnectorHealthService(
            logsDirectory: scoutDir.appendingPathComponent(".scout-logs"),
            ackStoreURL: scoutDir.appendingPathComponent(".scout-cache/connector-alerts-acked.json"),
            fileEvents: watcher
        )
        let logs = SessionLogService(
            logsDirectory: scoutDir.appendingPathComponent(".scout-logs"),
            trackerService: tracker,
            gitService: git,
            fileEvents: watcher
        )
        // Plan 5: scout-app no longer dispatches launchd plists. ScheduleService
        // polls `scoutctl schedule list-upcoming --json` every 60 s and renders
        // the upcoming-runs strip. Fire-now goes through `scoutctl schedule
        // fire-now <slot-key>` via the shared `runner`.
        //
        // `/usr/bin/env scoutctl` lets us resolve scoutctl via $PATH instead of
        // pinning to a conda/homebrew install path that may shift between
        // machines. Mirrors the ActionItemsWriter `python3` invocation pattern.
        let scoutctlExe = URL(fileURLWithPath: "/usr/bin/env")
        let scoutctlArgsPrefix = ["scoutctl"]
        let sched = ScheduleService(
            scoutctl: scoutctlExe,
            runner: runner,
            argumentsPrefix: scoutctlArgsPrefix
        )
        let power = PowerStateService(runner: runner)
        let editor = ScheduleEditorService(
            repoDirectory: scoutDir.appendingPathComponent("launchd"),
            agentsDirectory: FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/LaunchAgents"),
            userUid: getuid(),
            launchctl: SystemLaunchctlClient(runner: runner),
            git: git,
            fileEvents: watcher
        )
        let notif = NotificationService()
        let ccSessions = ClaudeSessionService(
            projectsDirectory: ClaudeSessionService
                .defaultScoutSessionsDirectory(scoutDirectory: scoutDir)
        )

        let docService = ActionItemsDocumentService(directory: actionItemsDir, fileEvents: watcher)
        let writerActor = ActionItemsWriter(
            python3: URL(fileURLWithPath: "/usr/bin/env"),
            actionItemsDirectory: actionItemsDir,
            scoutDirectory: scoutDir,
            runner: runner,
            gitService: git
        )
        let writerBox = ActionItemsWriterBox(writer: writerActor)
        let envState = ActionItemsEnvironmentState()

        self.fileWatcher = watcher
        self.gitService = git
        self.trackerService = tracker
        self.sessionTokensService = tokens
        self.connectorHealthService = connectorHealth
        self.sessionLogService = logs
        self.scheduleService = sched
        self.powerStateService = power
        self.scheduleEditorService = editor
        self.notificationService = notif
        self.claudeSessionService = ccSessions
        self.actionItemsDocumentService = docService
        self.actionItemsWriterBox = writerBox
        self.actionItemsEnvState = envState
        self.scoutDirectory = scoutDir
        self.actionItemsDirectory = actionItemsDir
        self.runner = runner
        self.scoutctlExecutable = scoutctlExe

        Task { [weak self] in
            _ = try? await tracker.loadInitial()
            _ = try? await tokens.loadInitial()
            _ = try? await connectorHealth.loadInitial()
            _ = try? await logs.loadInitial()
            await MainActor.run {
                sched.start()
                power.start()
            }
            _ = try? await editor.loadAll()
            await MainActor.run { editor.startWatching() }
            await self?.recomputeMenuStatus()

            // Run environment check; publish result.
            let check = ActionItemsEnvironmentCheck(
                actionItemsDirectory: actionItemsDir,
                runner: runner
            )
            if let result = try? await check.run() {
                await MainActor.run { envState.result = result }
            }
        }

        startNotificationWatch()
    }

    /// Fire a slot immediately via `scoutctl schedule fire-now <slot-key>`.
    /// Plan 5 removed in-app dispatch — the engine now owns slot routing,
    /// scout-app just shells out. Errors are swallowed for parity with the
    /// old `runnerService.runNow` (which also returned `try? await`).
    func fireNow(slotKey: String, bypassBudget: Bool = false) async {
        var args = ["scoutctl", "schedule", "fire-now", slotKey]
        if bypassBudget { args.append("--bypass-budget") }
        _ = try? await runner.run(
            executable: scoutctlExecutable,
            arguments: args,
            environment: [:],
            workingDirectory: scoutDirectory
        )
    }

    func recomputeMenuStatus() async {
        let latest = sessionLogService.runs.first
        let next: MenuBarStatus = switch latest?.status {
        case .running: .running
        case .failure, .timeout, .rateLimited: .lastFailed
        case .skippedBudget: .budgetSkipped
        default: .idle
        }
        menuBarStatus = next
    }

    private func startNotificationWatch() {
        sessionLogService.$runs.sink { [weak self] runs in
            guard let self else { return }
            Task { @MainActor in
                for r in runs {
                    let prev = self.previousStatus[r.id]
                    if prev == .running,
                       r.status != .running,
                       r.status != .success {
                        self.notificationService.notify(run: r)
                    }
                    self.previousStatus[r.id] = r.status
                }
                await self.recomputeMenuStatus()
            }
        }.store(in: &cancellables)
    }
}
