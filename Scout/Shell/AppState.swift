import Foundation
import SwiftUI
import Combine

@MainActor
final class AppState: ObservableObject {
    enum MenuBarStatus { case idle, running, lastFailed, budgetSkipped }

    @Published var menuBarStatus: MenuBarStatus = .idle

    /// Last "Run now" (fire-now) failure, surfaced to the UI. Set when a
    /// `scoutctl schedule fire-now` invocation throws or exits non-zero;
    /// cleared on the next successful fire (issue #45 — previously swallowed).
    @Published var fireNowError: String? = nil

    // Existing Control Center services
    let fileWatcher: FileWatcher
    let trackerService: UsageTrackerService
    let sessionTokensService: SessionTokensService
    let connectorHealthService: ConnectorHealthService
    let sessionLogService: SessionLogService
    let scheduleService: ScheduleService
    let powerStateService: PowerStateService
    let scheduleEditService: ScheduleEditService
    let gitService: GitService
    let notificationService: NotificationService
    let claudeSessionService: ClaudeSessionService

    // Process runner kept at app level so fire-now shell-outs (UpcomingStripView,
    // RunDetailView, MenuBarExtraContent) can invoke `scoutctl schedule fire-now`
    // without each consumer constructing its own runner.
    let runner: any ProcessRunner
    let scoutctlExecutable: URL
    /// Args inserted before scoutctl subcommands. Empty when `scoutctlExecutable`
    /// is scoutctl itself; `["scoutctl"]` when we fell back to `/usr/bin/env`.
    /// Every scoutctl shell-out must use this — see `fireNowArguments`.
    let scoutctlArgumentsPrefix: [String]

    // New Action Items services
    let actionItemsDocumentService: ActionItemsDocumentService
    let actionItemsWriterBox: ActionItemsWriterBox
    let actionItemsEnvState: ActionItemsEnvironmentState
    let scoutDirectory: URL
    let actionItemsDirectory: URL

    // Proposals (dreaming-proposals.md review)
    let proposalsDocumentService: ProposalsDocumentService
    let proposalsWriterBox: ProposalsWriterBox

    // Reply Drafts (drafts/ review — prepared replies the user owes)
    let replyDraftsDocumentService: ReplyDraftsDocumentService
    let replyDraftsWriterBox: ReplyDraftsWriterBox
    /// Per-draft AI assistant chat (shells out to the user's `claude` CLI).
    let replyChatService: ReplyChatService

    // Per-file Wishlist + Research tabs
    let wishlistDocumentService: PerFileDocumentService
    let researchDocumentService: PerFileDocumentService
    let perFileWriterBox: PerFileItemWriterBox

    private var previousStatus: [Run.ID: RunStatus] = [:]
    private var cancellables: Set<AnyCancellable> = []

    init() {
        let scoutDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Scout")
        let actionItemsDir = scoutDir.appendingPathComponent("action-items")
        let watcher = FileWatcher()
        let runner = SystemProcessRunner()

        // Resolve scoutctl explicitly. When Scout.app launches from Finder
        // (or via `open`), its PATH is the LaunchServices default
        // (`/usr/bin:/bin:/usr/sbin:/sbin`) — homebrew, miniconda, pipx,
        // and the scout-plugin bin dir are all absent. `/usr/bin/env
        // scoutctl` then fails silently inside ScheduleService.refresh
        // (caught by the do/catch), leaving the upcoming strip empty.
        //
        // Pick the first concrete scoutctl on disk so we don't depend on
        // GUI app PATH inheritance at all. Falls back to `/usr/bin/env`
        // only if no known path exists (then ScheduleService surfaces the
        // exec error via its `lastError` publisher so the UI can show
        // "scoutctl not found").
        let scoutctlResolved = AppState.resolveScoutctlPath()

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
        let scoutctlExe = scoutctlResolved.executable
        let scoutctlArgsPrefix = scoutctlResolved.argsPrefix
        let sched = ScheduleService(
            scoutctl: scoutctlExe,
            runner: runner,
            argumentsPrefix: scoutctlArgsPrefix
        )
        let power = PowerStateService(runner: runner)
        let canonical = scoutDir
            .appendingPathComponent(".scout-state")
            .appendingPathComponent("schedule.yaml")
        let scheduleEditService = ScheduleEditService(
            scoutctl: scoutctlExe,
            runner: runner,
            canonicalSchedulePath: canonical,
            argumentsPrefix: scoutctlArgsPrefix
        )
        let notif = NotificationService()
        let ccSessions = ClaudeSessionService(
            projectsDirectory: ClaudeSessionService
                .defaultScoutSessionsDirectory(scoutDirectory: scoutDir)
        )

        let docService = ActionItemsDocumentService(directory: actionItemsDir, fileEvents: watcher)
        let writerActor = ActionItemsWriter(
            scoutctl: scoutctlExe,
            argumentsPrefix: scoutctlArgsPrefix,
            actionItemsDirectory: actionItemsDir,
            scoutDirectory: scoutDir,
            runner: runner,
            gitService: git
        )
        let writerBox = ActionItemsWriterBox(writer: writerActor)
        let envState = ActionItemsEnvironmentState()

        // Per-file proposals live in `dreaming-proposals/` (the sibling
        // `dreaming-proposals.md` is just an index). The folder is overridable
        // via the `dreamingProposalsPath` setting; takes effect on next launch.
        let proposalsDirURL: URL = {
            let override = UserDefaults.standard
                .string(forKey: "dreamingProposalsPath")?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let override, !override.isEmpty {
                return URL(fileURLWithPath: (override as NSString).expandingTildeInPath)
            }
            return scoutDir.appendingPathComponent("dreaming-proposals")
        }()
        let proposalsDoc = ProposalsDocumentService(directoryURL: proposalsDirURL, fileEvents: watcher)
        let proposalsWriter = ProposalsWriter(
            scoutDirectory: scoutDir,
            gitService: git
        )
        let proposalsWriterBox = ProposalsWriterBox(writer: proposalsWriter)

        // Reply drafts live in `drafts/` (the sibling `drafts/README.md` is just
        // a doc with no frontmatter, so the parser skips it). The folder is
        // overridable via the `replyDraftsPath` setting; takes effect on next
        // launch. Mirrors the dreamingProposalsPath pattern.
        let replyDraftsDirURL: URL = {
            let override = UserDefaults.standard
                .string(forKey: "replyDraftsPath")?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let override, !override.isEmpty {
                return URL(fileURLWithPath: (override as NSString).expandingTildeInPath)
            }
            return scoutDir.appendingPathComponent("drafts")
        }()
        let replyDraftsDoc = ReplyDraftsDocumentService(directoryURL: replyDraftsDirURL, fileEvents: watcher)
        let replyDraftsWriter = ReplyDraftsWriter(scoutDirectory: scoutDir, gitService: git)
        let replyDraftsWriterBox = ReplyDraftsWriterBox(writer: replyDraftsWriter)

        // Per-draft AI chat shells out to the user's claude CLI (their license).
        let claudeResolved = AppState.resolveClaudePath()
        let replyChat = ReplyChatService(
            runner: runner,
            claude: claudeResolved.executable,
            claudeArgsPrefix: claudeResolved.argsPrefix,
            workingDirectory: scoutDir
        )

        // Per-file Wishlist + Research: resolve directory (override key or default
        // relative path under scoutDir), matching the dreamingProposalsPath pattern.
        func perFileDir(_ config: PerFileTabConfig) -> URL {
            let override = UserDefaults.standard
                .string(forKey: config.pathOverrideKey)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let override, !override.isEmpty {
                return URL(fileURLWithPath: (override as NSString).expandingTildeInPath)
            }
            return scoutDir.appendingPathComponent(config.directoryDefaultRelative)
        }
        let wishlistDoc = PerFileDocumentService(directoryURL: perFileDir(.wishlist), fileEvents: watcher)
        let researchDoc = PerFileDocumentService(directoryURL: perFileDir(.research), fileEvents: watcher)
        let perFileWriter = PerFileItemWriter(scoutDirectory: scoutDir, gitService: git)
        let perFileWriterBox = PerFileItemWriterBox(writer: perFileWriter)

        self.fileWatcher = watcher
        self.gitService = git
        self.trackerService = tracker
        self.sessionTokensService = tokens
        self.connectorHealthService = connectorHealth
        self.sessionLogService = logs
        self.scheduleService = sched
        self.powerStateService = power
        self.scheduleEditService = scheduleEditService
        self.notificationService = notif
        self.claudeSessionService = ccSessions
        self.actionItemsDocumentService = docService
        self.actionItemsWriterBox = writerBox
        self.actionItemsEnvState = envState
        self.proposalsDocumentService = proposalsDoc
        self.proposalsWriterBox = proposalsWriterBox
        self.replyDraftsDocumentService = replyDraftsDoc
        self.replyDraftsWriterBox = replyDraftsWriterBox
        self.replyChatService = replyChat
        self.wishlistDocumentService = wishlistDoc
        self.researchDocumentService = researchDoc
        self.perFileWriterBox = perFileWriterBox
        self.scoutDirectory = scoutDir
        self.actionItemsDirectory = actionItemsDir
        self.runner = runner
        self.scoutctlExecutable = scoutctlExe
        self.scoutctlArgumentsPrefix = scoutctlArgsPrefix

        // Forward child-service changes so AppState.objectWillChange fires when
        // wishlist/research item counts update (drives sidebar badge reactivity).
        // DispatchQueue.main avoids badge lag that can occur with RunLoop.main
        // during modal run-loop tracking.
        wishlistDoc.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        researchDoc.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        replyDraftsDoc.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)

        Task { [weak self] in
            _ = try? await tracker.loadInitial()
            _ = try? await tokens.loadInitial()
            _ = try? await connectorHealth.loadInitial()
            _ = try? await logs.loadInitial()
            await MainActor.run {
                sched.start()
                power.start()
                // Load proposals at launch so the sidebar badge is populated
                // before the user opens the Proposals section.
                proposalsDoc.load()
                // Load wishlist + research so their badges are ready on launch.
                wishlistDoc.load()
                researchDoc.load()
                // Load reply drafts so the badge is ready on launch.
                replyDraftsDoc.load()
            }
            await self?.recomputeMenuStatus()

            // Run environment check; publish result.
            let check = ActionItemsEnvironmentCheck(
                scoutctl: scoutctlExe,
                argumentsPrefix: scoutctlArgsPrefix,
                runner: runner
            )
            if let result = try? await check.run() {
                await MainActor.run { envState.result = result }
            }
        }

        startNotificationWatch()
    }

    /// Shells out to `scoutctl schedule fire-now <slotKey>`, optionally
    /// bypassing the engine's daily-spend gate via `--bypass-budget`.
    ///
    /// Plan 5 removed in-app dispatch — the engine now owns slot routing,
    /// scout-app just shells out. Errors are swallowed for parity with the
    /// old `runnerService.runNow` (which also returned `try? await`).
    ///
    /// `bypassBudget: true` is used by `RunDetailView` for the "force retry"
    /// path — a manual override that lets a slot fire even when the day's
    /// budget has already been spent. Default `false` for normal upcoming-strip
    /// run-now buttons (which respect the budget gate).
    ///
    /// After the dispatch returns, immediately refresh `ScheduleService` so
    /// the heartbeat strip drops the just-fired slot instead of sitting on
    /// the past `scheduled_at` until the next 60 s poll tick.
    func fireNow(slotKey: String, bypassBudget: Bool = false) async {
        let args = Self.fireNowArguments(
            argumentsPrefix: scoutctlArgumentsPrefix,
            slotKey: slotKey,
            bypassBudget: bypassBudget
        )
        do {
            let result = try await runner.run(
                executable: scoutctlExecutable,
                arguments: args,
                environment: [:],
                workingDirectory: scoutDirectory
            )
            if result.exitCode != 0 {
                let stderr = String(data: result.stderr, encoding: .utf8) ?? ""
                let detail = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
                fireNowError = "Run now failed (exit \(result.exitCode))"
                    + (detail.isEmpty ? "" : ": \(detail)")
            } else {
                fireNowError = nil
            }
        } catch {
            fireNowError = "Run now failed: \(error.localizedDescription)"
        }
        await scheduleService.refresh()
    }

    /// Build the argv for `scoutctl schedule fire-now`. argv[0] must be the
    /// resolved `argumentsPrefix` (empty for an absolute scoutctl path,
    /// `["scoutctl"]` for the `/usr/bin/env` fallback) — never a hardcoded
    /// "scoutctl", which an absolute-path executable would receive as a bogus
    /// subcommand (issue #45).
    nonisolated static func fireNowArguments(
        argumentsPrefix: [String],
        slotKey: String,
        bypassBudget: Bool
    ) -> [String] {
        var args = argumentsPrefix + ["schedule", "fire-now", slotKey]
        if bypassBudget { args.append("--bypass-budget") }
        return args
    }

    /// Where scoutctl lives + how to invoke it. Used by the constructor to
    /// wire ScheduleService and ScheduleEditService at startup.
    struct ScoutctlInvocation {
        /// Executable to launch. If we found scoutctl on disk this is its
        /// absolute path; otherwise `/usr/bin/env` and we lean on $PATH.
        let executable: URL
        /// Args inserted before the user's args. Empty when `executable`
        /// is scoutctl itself; `["scoutctl"]` when we fell back to
        /// `/usr/bin/env`.
        let argsPrefix: [String]
    }

    /// Try known install paths in priority order. The scout-plugin repo's
    /// own `bin/` is preferred because it's the canonical source of truth;
    /// after that we walk the locations the user is likely to have
    /// installed scoutctl via (miniconda, pipx, homebrew, /usr/local). If
    /// none exist, fall back to `/usr/bin/env scoutctl` so a user with
    /// scoutctl on PATH (e.g. running from Xcode-inherited env) still
    /// works.
    static func resolveScoutctlPath() -> ScoutctlInvocation {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates: [URL] = [
            home.appendingPathComponent("scout-plugin/bin/scoutctl"),
            home.appendingPathComponent("miniconda3/bin/scoutctl"),
            home.appendingPathComponent(".local/bin/scoutctl"),
            URL(fileURLWithPath: "/opt/homebrew/bin/scoutctl"),
            URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
        ]
        let fm = FileManager.default
        for url in candidates {
            if fm.isExecutableFile(atPath: url.path) {
                return ScoutctlInvocation(executable: url, argsPrefix: [])
            }
        }
        return ScoutctlInvocation(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            argsPrefix: ["scoutctl"]
        )
    }

    /// Locate the `claude` CLI the same way we locate scoutctl — the per-draft
    /// chat shells out to it so it runs on the user's own Claude license. Tries
    /// known install paths; falls back to `/usr/bin/env claude` if none exist.
    static func resolveClaudePath() -> ScoutctlInvocation {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates: [URL] = [
            home.appendingPathComponent(".local/bin/claude"),
            home.appendingPathComponent(".claude/local/claude"),
            URL(fileURLWithPath: "/opt/homebrew/bin/claude"),
            URL(fileURLWithPath: "/usr/local/bin/claude"),
        ]
        let fm = FileManager.default
        for url in candidates where fm.isExecutableFile(atPath: url.path) {
            return ScoutctlInvocation(executable: url, argsPrefix: [])
        }
        return ScoutctlInvocation(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            argsPrefix: ["claude"]
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
