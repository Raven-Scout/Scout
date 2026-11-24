import AppKit
import Foundation
import SwiftUI
import Combine
import os

@MainActor
final class AppState: ObservableObject {
    /// What the menu-bar icon, the menu-bar panel and the status bar show
    /// (card C3). Derived from the engine's health and the latest run by
    /// `recomputeStatus()`; settable so view tests can pin a state.
    @Published var status = ScoutStatus(kind: .checking, title: "Checking Scout…", detail: "Looking at the engine", tone: .neutral)

    /// Last "Run now" (fire-now) failure, surfaced to the UI. Set when a
    /// `scoutctl schedule fire-now` invocation throws or exits non-zero;
    /// cleared on the next successful fire (issue #45 — previously swallowed).
    @Published var fireNowError: String? = nil

    /// A run another tab asked to open (the Wishlist/Research history pane).
    /// ControlCenterView opens its detail and clears this. (#43)
    @Published var pendingRunToOpen: Run.ID? = nil

    /// A sidebar tab another view asked to switch to. MainWindowView applies
    /// it to its selection and clears it. (#43)
    @Published var requestedSidebar: SidebarItem? = nil
    /// A KB note another tab asked the Knowledge Base to open, by wikilink
    /// target; the KB clears it once handled (Raven-Scout/Scout#295).
    @Published var pendingKBNote: String? = nil
    @Published private(set) var firingSlotKeys: Set<String> = []
    @Published private(set) var urgentActionCount: Int = 0
    /// Sessions that need you — the Sessions sidebar badge. Forwarded from
    /// `sessionIndexService.needsYouCount` alone, so the window does not
    /// re-render on every index refresh.
    @Published private(set) var sessionsNeedsYouCount: Int = 0

    // Existing Control Center services
    /// The FSEvents source every document service watches through. Production
    /// wires a real `FileWatcher`; tests inject a fake so no vault is touched.
    let fileEvents: any FileSystemEventSource
    let trackerService: UsageTrackerService
    /// Scout's own scheduled-run usage (`scoutctl budget usage --json`).
    let scoutUsageService: ScoutUsageService
    let connectorHealthService: ConnectorHealthService
    let sessionLogService: SessionLogService
    let scheduleService: ScheduleService
    let powerStateService: PowerStateService
    let scheduleEditService: ScheduleEditService
    let budgetSettingsService: BudgetSettingsService
    let modelSettingsService: ModelSettingsService
    /// Action Items display options from `scout-display.json` in the vault root (#52).
    let displayProfileService: DisplayProfileService
    let gitService: GitService
    let notificationService: any RunNotificationPosting
    /// Which runs' notifications to post, replace or take back.
    let runNotifications: RunNotificationTracker
    let claudeSessionService: ClaudeSessionService
    /// The Sessions page's `scoutctl session index` driver.
    let sessionIndexService: SessionIndexService

    // Process runner kept at app level so fire-now shell-outs (UpcomingStripView,
    // RunDetailView, MenuBarExtraContent) can invoke `scoutctl schedule fire-now`
    // without each consumer constructing its own runner.
    let runner: any ProcessRunner
    let scoutctlExecutable: URL
    /// Args inserted before scoutctl subcommands. Always empty now that
    /// `scoutctlExecutable` is always a concrete resolved path (`EngineLocator`
    /// or the shim), never `/usr/bin/env`. Every scoutctl shell-out must use
    /// this — see `fireNowArguments`.
    let scoutctlArgumentsPrefix: [String]

    // Engine discovery (spec §4.4)
    let engineLayout: EngineLayout
    let engineHealth: EngineHealthService
    /// Settings ▸ Engine ▸ Run protection (card C9): `scoutctl protection status`, the privacy-check
    /// re-check and the owner's write-protection switch.
    let runProtection: RunProtectionService
    /// Settings ▸ Agent copies (#550): `scoutctl delegate status --json`, and the CLI's own
    /// Preview and Sync now for a delegate mirror. Nothing else about delegates runs from the app.
    let delegateStatus: DelegateStatusService
    let inboxReview: InboxReviewService
    /// The run ledger (`scoutctl runs --json`): the Health page, and the
    /// Control Center's run statuses for runs the ledger records.
    let runLedgerService: RunLedgerService
    /// The Changes tab: the changes map read through `scoutctl` (read-only), and the owner's decisions on it,
    /// which are `scoutctl` calls too (`--surface app`).
    let changesService: ChangesService
    let changesDecisions: ChangesDecisions
    /// "Resolve with Claude" (`scoutctl health resolve-prompt`): the engine-built investigation
    /// prompt behind health, connector and run banners. Views read it from the environment.
    let resolvePromptService: ResolvePromptService

    // Engine install + upgrade (spec §5)
    /// The engine this build ships; nil only when the resource is missing
    /// (never in a real build) or in a test configuration (Ruling 46).
    let engineRelease: EngineRelease?
    private let engineTarballURL: URL?
    private let resolveClaude: @Sendable (String) -> String?
    private let fileDownloader: any FileDownloader
    /// Latest progress per step of the running (or last failed) engine
    /// upgrade. Non-nil while the upgrade sheet is showing.
    @Published private(set) var engineUpgradeProgress: [InstallStep: InstallProgress]?
    /// Why the upgrade could not start or stopped without a failed step.
    @Published private(set) var engineUpgradeError: String?
    @Published private(set) var isUpgradingEngine = false
    /// True while the running (or last) upgrade targets the version already
    /// installed — finishing a switch or repairing — so the sheet says so.
    @Published private(set) var engineUpgradeIsRepair = false
    /// `EngineUpgrader.hasUnfinishedSwitch` for the current state (Ruling 69
    /// I1): an earlier upgrade's `bootstrap upgrade` ran but Claude Code was
    /// never switched over. Re-read on every engine refresh.
    @Published private(set) var engineSwitchUnfinished = false
    /// The one onboarding flow (Ruling 41): present while the engine gates
    /// the tabs (or while a finishing flow holds the window). The window's
    /// gate and Settings ▸ Engine's sheet show this same model, so the flow
    /// is never rebuilt per render and never runs twice at once.
    @Published private(set) var onboarding: OnboardingViewModel? {
        didSet { watchOnboarding() }
    }
    private var onboardingWatch: AnyCancellable?
    /// Whether the last engine state seen gated the tabs — a gated → usable
    /// transition re-runs the Action Items environment check (Ruling 69 I3).
    private var lastGatesTabs = false
    private var upgradeGeneration = 0
    private var liveUpgradeGeneration: Int?

    // New Action Items services
    let actionItemsDocumentService: ActionItemsDocumentService
    let actionItemsWriterBox: ActionItemsWriterBox
    /// Undo for vault writes, shared by every page in the main window (C10).
    let vaultUndo = VaultUndoController()
    let actionItemsEnvState: ActionItemsEnvironmentState
    let scoutDirectory: URL
    let actionItemsDirectory: URL
    /// Backing store for the user's settings. Held so main-actor reads (the
    /// inline-comment byline `refreshUrgentActionCount` hands the parser) use
    /// the same store the rest of the app does, and tests can substitute one.
    private let defaults: UserDefaults

    // Proposals (dreaming-proposals.md review)
    let proposalsDocumentService: ProposalsDocumentService
    let proposalsWriterBox: ProposalsWriterBox

    // Per-file Wishlist + Research tabs
    let wishlistDocumentService: PerFileDocumentService
    let researchDocumentService: PerFileDocumentService
    let perFileWriterBox: PerFileItemWriterBox

    // Knowledge Base (browse + edit ~/Scout/knowledge-base/)
    let knowledgeBaseService: KnowledgeBaseService
    let knowledgeBaseWriterBox: KnowledgeBaseWriterBox
    /// Privacy labels for the KB tree's lock badge, via `scoutctl kb classify`.
    let knowledgeBasePrivacy: KBPrivacyLabelService

    private var cancellables: Set<AnyCancellable> = []

    /// Production entry point — the vault root resolves per spec §4.4 (the
    /// `scoutDataDir` default, then the engine pointer's `vault`, then
    /// `~/Scout`) and all the background work (timers, file watches,
    /// launch-time loads) starts.
    convenience init() {
        self.init(configuration: .production())
    }

    /// Designated initializer. Every external dependency arrives through
    /// `configuration`, so tests can point the whole object graph at a temp
    /// directory and keep the background work switched off.
    init(configuration: Configuration) {
        let scoutDir = configuration.scoutDirectory
        let actionItemsDir = scoutDir.appendingPathComponent("action-items")
        let events = configuration.fileEvents
        let runner = configuration.runner
        let defaults = configuration.defaults

        // The engine is found by `EngineLocator` (pointer → conventional
        // layout → shim → marketplace cache → dev checkout) inside
        // `Configuration.production()`, not here — the app no longer guesses
        // a `scoutctl` path or falls back to `/usr/bin/env` + PATH luck. When
        // nothing is found, `production()` hands us the shim path instead:
        // ENOENT there is the honest failure, and `EngineHealthService`
        // reports the same fact so the UI isn't silently broken.
        let scoutctlResolved = configuration.scoutctl
        let engineHealth = EngineHealthService(
            locator: EngineLocator(layout: configuration.engineLayout),
            runner: runner,
            environment: ["SCOUT_DATA_DIR": scoutDir.path],
            initialState: configuration.initialEngineState
        )

        // Asks the engine health for scoutctl each time, so it follows the engine that's found; none while setup gates the tabs.
        let runProtection = RunProtectionService(
            runner: runner,
            environment: ["SCOUT_DATA_DIR": scoutDir.path],
            scoutctl: { [weak engineHealth] in
                guard let state = engineHealth?.state, !state.gatesTabs else { return nil }
                if case .broken = state { return nil }
                return state.scoutctl
            }
        )
        // Settings ▸ Agent copies (#550): the same engine, the same way.
        let delegateStatus = DelegateStatusService(
            runner: runner,
            environment: ["SCOUT_DATA_DIR": scoutDir.path],
            scoutctl: { [weak engineHealth] in
                guard let state = engineHealth?.state, !state.gatesTabs else { return nil }
                if case .broken = state { return nil }
                return state.scoutctl
            }
        )

        let runLedger = RunLedgerService(
            scoutctl: scoutctlResolved.executable,
            argumentsPrefix: scoutctlResolved.argsPrefix,
            runner: runner,
            environment: ["SCOUT_DATA_DIR": scoutDir.path],
            logsDirectory: scoutDir.appendingPathComponent(".scout-logs"),
            fileEvents: events
        )
        let resolvePrompt = ResolvePromptService(
            scoutctl: scoutctlResolved.executable,
            argumentsPrefix: scoutctlResolved.argsPrefix,
            runner: runner,
            environment: ["SCOUT_DATA_DIR": scoutDir.path],
            scoutDirectory: scoutDir
        )

        let git = GitService(repoURL: scoutDir, runner: runner)
        let tracker = UsageTrackerService(
            trackerURL: scoutDir.appendingPathComponent(".scout-logs/usage-tracker.jsonl"),
            fileEvents: events
        )
        let connectorHealth = ConnectorHealthService(
            logsDirectory: scoutDir.appendingPathComponent(".scout-logs"),
            ackStoreURL: scoutDir.appendingPathComponent(".scout-cache/connector-alerts-acked.json"),
            fileEvents: events
        )
        let logs = SessionLogService(
            logsDirectory: scoutDir.appendingPathComponent(".scout-logs"),
            trackerService: tracker,
            gitService: git,
            fileEvents: events,
            parseCacheURL: configuration.parseCacheURL
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
            argumentsPrefix: scoutctlArgsPrefix,
            drafts: .shared
        )
        // Budget config is read and written through `scoutctl budget show/set`,
        // never by parsing scout-config.yaml here — that file doubles as
        // bootstrap state with several producers.
        let budgetSettings = BudgetSettingsService(
            scoutctl: scoutctlExe,
            runner: runner,
            argumentsPrefix: scoutctlArgsPrefix
        )
        // Model defaults go through `scoutctl model show/set-default` for the
        // same reason; a slot's own model is saved with the slot.
        let modelSettings = ModelSettingsService(
            scoutctl: scoutctlExe,
            runner: runner,
            argumentsPrefix: scoutctlArgsPrefix
        )
        let usage = ScoutUsageService(
            scoutctl: scoutctlExe,
            runner: runner,
            argumentsPrefix: scoutctlArgsPrefix
        )
        let notif = configuration.notifications ?? NotificationService()
        let ccSessions = ClaudeSessionService(
            projectsDirectory: configuration.claudeSessionsDirectory
        )
        let sessionIndex = SessionIndexService(configuration: .init(
            scoutctl: scoutctlExe,
            argumentsPrefix: scoutctlArgsPrefix,
            runner: runner,
            fileEvents: events,
            indexFile: scoutDir.appendingPathComponent(".scout-cache/sessions-index.json"),
            watchRoots: configuration.agentSessionWatchRoots,
            source: configuration.agentSessionSource
        ))

        let docService = ActionItemsDocumentService(
            directory: actionItemsDir, fileEvents: events, defaults: defaults
        )
        let displayProfile = DisplayProfileService(scoutDirectory: scoutDir, fileEvents: events)
        let writerActor = ActionItemsWriter(
            scoutctl: scoutctlExe,
            argumentsPrefix: scoutctlArgsPrefix,
            actionItemsDirectory: actionItemsDir,
            scoutDirectory: scoutDir,
            runner: runner,
            gitService: git
        )
        let boardWriter = BoardWriter(
            scoutctl: scoutctlExe,
            argumentsPrefix: scoutctlArgsPrefix,
            actionItemsDirectory: actionItemsDir,
            scoutDirectory: scoutDir,
            runner: runner,
            git: git
        )
        let writerBox = ActionItemsWriterBox(writer: writerActor, boardWriter: boardWriter)
        let envState = ActionItemsEnvironmentState()

        // Per-file proposals live in `dreaming-proposals/` (the sibling
        // `dreaming-proposals.md` is just an index). The folder is overridable
        // via the `dreamingProposalsPath` setting; takes effect on next launch.
        let proposalsDirURL: URL = {
            let override = defaults
                .string(forKey: "dreamingProposalsPath")?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let override, !override.isEmpty {
                return URL(fileURLWithPath: (override as NSString).expandingTildeInPath)
            }
            return scoutDir.appendingPathComponent("dreaming-proposals")
        }()
        let proposalsDoc = ProposalsDocumentService(directoryURL: proposalsDirURL, fileEvents: events)
        let proposalsWriter = ProposalsWriter(
            scoutDirectory: scoutDir,
            gitService: git
        )
        let proposalsWriterBox = ProposalsWriterBox(writer: proposalsWriter)

        // Per-file Wishlist + Research: resolve directory (override key or default
        // relative path under scoutDir), matching the dreamingProposalsPath pattern.
        func perFileDir(_ config: PerFileTabConfig) -> URL {
            let override = defaults
                .string(forKey: config.pathOverrideKey)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let override, !override.isEmpty {
                return URL(fileURLWithPath: (override as NSString).expandingTildeInPath)
            }
            return scoutDir.appendingPathComponent(config.directoryDefaultRelative)
        }
        let wishlistDoc = PerFileDocumentService(directoryURL: perFileDir(.wishlist), fileEvents: events)
        let researchDoc = PerFileDocumentService(directoryURL: perFileDir(.research), fileEvents: events)
        let perFileWriter = PerFileItemWriter(scoutDirectory: scoutDir, gitService: git)
        let perFileWriterBox = PerFileItemWriterBox(writer: perFileWriter)

        // Knowledge Base: tree service over `knowledge-base/` + whole-file writer.
        let kbService = KnowledgeBaseService(scoutDirectory: scoutDir, fileEvents: events)
        let kbWriter = KnowledgeBaseFileWriter(scoutDirectory: scoutDir, gitService: git)
        let kbWriterBox = KnowledgeBaseWriterBox(writer: kbWriter)
        let kbPrivacy = KBPrivacyLabelService(
            scoutctl: scoutctlExe,
            runner: runner,
            argumentsPrefix: scoutctlArgsPrefix,
            vault: kbService.scoutDirectory
        )

        self.fileEvents = events
        self.gitService = git
        self.trackerService = tracker
        self.scoutUsageService = usage
        self.connectorHealthService = connectorHealth
        self.sessionLogService = logs
        self.scheduleService = sched
        self.powerStateService = power
        self.scheduleEditService = scheduleEditService
        self.budgetSettingsService = budgetSettings
        self.modelSettingsService = modelSettings
        self.displayProfileService = displayProfile
        self.notificationService = notif
        self.runNotifications = RunNotificationTracker(poster: notif)
        self.claudeSessionService = ccSessions
        self.sessionIndexService = sessionIndex
        self.actionItemsDocumentService = docService
        self.actionItemsWriterBox = writerBox
        self.actionItemsEnvState = envState
        self.proposalsDocumentService = proposalsDoc
        self.proposalsWriterBox = proposalsWriterBox
        self.wishlistDocumentService = wishlistDoc
        self.researchDocumentService = researchDoc
        self.perFileWriterBox = perFileWriterBox
        self.knowledgeBaseService = kbService
        self.knowledgeBaseWriterBox = kbWriterBox
        self.knowledgeBasePrivacy = kbPrivacy
        self.scoutDirectory = scoutDir
        self.actionItemsDirectory = actionItemsDir
        self.defaults = defaults
        self.runner = runner
        self.scoutctlExecutable = scoutctlExe
        self.scoutctlArgumentsPrefix = scoutctlArgsPrefix
        self.engineLayout = configuration.engineLayout
        self.engineHealth = engineHealth
        self.runProtection = runProtection
        self.delegateStatus = delegateStatus
        // One engine gate for every service that follows the found engine and runs nothing while setup gates the
        // tabs or the engine is broken: Settings ▸ Agent copies ▸ Review (#550 V4b3) and the Changes tab.
        let gatedEngine: @MainActor () -> URL? = { [weak engineHealth] in
            guard let state = engineHealth?.state, !state.gatesTabs else { return nil }
            if case .broken = state { return nil }
            return state.scoutctl
        }
        self.inboxReview = InboxReviewService(
            runner: runner,
            environment: ["SCOUT_DATA_DIR": scoutDir.path],
            scoutctl: gatedEngine
        )
        self.runLedgerService = runLedger
        let changesService = ChangesService(
            scoutctl: gatedEngine,
            runner: runner,
            environment: ["SCOUT_DATA_DIR": scoutDir.path],
            logsDirectory: scoutDir.appendingPathComponent(".scout-logs"),
            pendingDirectory: scoutDir.appendingPathComponent("governance-pending"),
            fileEvents: events
        )
        self.changesService = changesService
        self.changesDecisions = ChangesDecisions(
            runner: runner,
            environment: ["SCOUT_DATA_DIR": scoutDir.path],
            scoutctl: gatedEngine
        )
        self.resolvePromptService = resolvePrompt
        self.engineRelease = configuration.engineRelease
        self.engineTarballURL = configuration.engineTarballURL
        self.resolveClaude = configuration.resolveClaude
        self.fileDownloader = configuration.fileDownloader

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
        // Same forwarding for the engine health service — drives the window
        // gate and the Settings sidebar badge (spec §5) through AppState's
        // own objectWillChange rather than requiring every observer to also
        // hold an @ObservedObject on engineHealth directly.
        engineHealth.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        // The status bar's cost cell and the usage card read the usage
        // summary through AppState.
        usage.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        // The onboarding gate, the unfinished-switch flag and the Action
        // Items check follow the engine state. Every refresh publishes (no
        // de-duplication): the switch flag reads the disk, which can change
        // while the state value doesn't.
        // The Health row's attention dot follows run health.
        runLedger.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        // The Changes row's badge counts what a loaded snapshot says needs you.
        changesService.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        // Runs the ledger records take its status (a safety skip is a skip,
        // not a run that hung); the rest keep their log-derived status.
        runLedger.$state
            .receive(on: DispatchQueue.main)
            .sink { [weak logs] state in
                guard case .loaded(let ledger) = state else { return }
                logs?.applyLedger(ledger.entriesByLog)
            }
            .store(in: &cancellables)
        // Which ledger keys are schedules: the full schedule (loaded with the
        // Schedules page) and the upcoming list (polled from launch). A
        // schedule the owner named `weekly-manual` is still a schedule in the
        // run list's name, badge and Type filter.
        sched.$upcoming.combineLatest(scheduleEditService.$slots)
            .map { upcoming, slots in Set(upcoming.map(\.slotKey)).union(slots.map(\.key)) }
            .removeDuplicates()
            .receive(on: DispatchQueue.main)
            .sink { [weak logs] keys in logs?.applyScheduleKeys(keys) }
            .store(in: &cancellables)
        engineHealth.$state
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.engineStateDidChange() }
            .store(in: &cancellables)
        // A failed Action Items check turns the page's writes off (R2), so it
        // must not stay failed after the engine works again: rerun it after
        // every engine health check (Settings ▸ Engine's Check now, the
        // periodic refresh) while it is failing. A passing one is left alone.
        engineHealth.$lastChecked
            .dropFirst()
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.recheckActionItemsEnvironmentIfFailing() }
            .store(in: &cancellables)
        actionItemsEnvState.recheck = { [weak self] in await self?.recheckActionItemsEnvironment() }
        // The shared status follows the engine as well as the runs (card C3).
        // `receive(on:)` hops past the willSet, so the service's properties
        // already hold the new values when this reads them.
        engineHealth.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.recomputeStatus() }
            .store(in: &cancellables)
        // The Control Center's Today column shows the real daily budget.
        budgetSettings.$settings
            .removeDuplicates()
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        sessionIndex.$needsYouCount
            .removeDuplicates()
            .receive(on: DispatchQueue.main)
            .sink { [weak self] count in self?.sessionsNeedsYouCount = count }
            .store(in: &cancellables)
        // Keep the menu-bar urgent badge live off the document the app has
        // already parsed (and re-parses on every write / watched change),
        // instead of relying solely on the panel's onAppear disk re-read —
        // MenuBarExtra(.window) does not guarantee onAppear re-fires per open.
        docService.$state
            .receive(on: DispatchQueue.main)
            .sink { [weak self] docState in
                guard case .loaded(let doc) = docState,
                      ActionItemsDay.stem(for: doc.date) == ActionItemsDay.stem(for: ActionItemsDay.today())
                else { return }
                self?.urgentActionCount = Self.urgentOpenCount(in: doc)
            }
            .store(in: &cancellables)

        // Correct from the first frame: the window shows onboarding at once
        // when the located engine gates the tabs. Building the model starts
        // no work — the view's `.task` does.
        lastGatesTabs = configuration.initialEngineState.gatesTabs
        syncOnboarding()
        refreshEngineSwitchState()
        recomputeStatus()

        // Everything below spawns work that outlives the initializer — polling
        // timers, FSEvents subscriptions, launch-time loads and a `scoutctl`
        // shell-out. Tests build the same object graph with this switched off
        // so a rendered view can't reach the filesystem or the network.
        guard configuration.startsBackgroundWork else { return }

        // The knowledge map reopens on its last settled layout after a
        // relaunch (UI audit T1). Only production names a file: a test graph
        // with background work on must not point the shared cache at the
        // real caches folder for every test after it.
        if let mapLayoutCacheURL = configuration.mapLayoutCacheURL {
            KBMapLayoutCache.file = mapLayoutCacheURL
        }

        // Loads the last index from disk, then keeps it current: the sidebar
        // badge needs it before the Sessions page is ever opened.
        sessionIndex.start()
        displayProfile.startWatching()
        // A Done range the Topics board kept in UserDefaults moves into
        // scout-display.json once (UI audit #46).
        displayProfile.migrateLegacyDoneRange(from: defaults)
        // schedule.yaml edited outside the app reloads the Schedules page
        // (UI audit #61), drafts kept.
        scheduleEditService.startWatching(events)

        // A finished run adds a runner row to the usage tracker, and the
        // tracker service re-reads the file on every change to it, so re-read
        // Scout's usage then. (Keying on the run log missed a runner row that
        // landed after the log said the run was done.) This relies on
        // `entries` publishing on EVERY assignment, even an equal one: it only
        // keeps `session` rows, so a runner row leaves it unchanged. Don't add
        // `removeDuplicates()` here or in UsageTrackerService. The launch read
        // (`tracker.loadInitial()` below) also lands here, which is the
        // launch-time usage refresh.
        tracker.$entries
            .dropFirst()
            .debounce(for: .seconds(1), scheduler: DispatchQueue.main)
            .sink { _ in Task { await usage.refresh() } }
            .store(in: &cancellables)

        // The run-log parse cache moved to a per-build, per-vault file; the
        // shipping app removes the old shared one (a dev build never does).
        Task.detached(priority: .utility) { SessionLogService.removeLegacyParseCacheAtLaunch() }

        Task { [weak self] in
            _ = try? await tracker.loadInitial()
            _ = try? await connectorHealth.loadInitial()
            _ = try? await logs.loadInitial()
            await runLedger.refresh()
            await changesService.refresh()
            await MainActor.run {
                runLedger.start()
                changesService.start()
                // Runs already on disk at launch never notify; only runs
                // that finish (or first appear) from here on do.
                self?.runNotificationsArmed = true
                sched.start()
                power.start()
                // Load proposals at launch so the sidebar badge is populated
                // before the user opens the Proposals section.
                proposalsDoc.load()
                // Load wishlist + research so their badges are ready on launch.
                wishlistDoc.load()
                researchDoc.load()
            }
            await self?.recomputeStatus()
            await self?.refreshUrgentActionCount()
            // The real daily budget for the Control Center (card C3); a
            // vault without one just leaves the budget off. Its own task, so
            // the engine check below never waits on it.
            Task { _ = try? await budgetSettings.load() }

            // Locate + doctor-check the engine concurrently with the Action
            // Items environment check below — a slow doctor (a real
            // subprocess round-trip) must not delay the Action Items banner.
            // The 10-minute re-check (spec §4.4) starts as soon as this first
            // refresh completes, inside the same child task — so a wedged
            // `scoutctl action-items --help` below can't hold it back. The
            // launch-time engine upgrade (spec §5) follows in that same child
            // task, for the same reason; it only ever runs here, inside
            // `startsBackgroundWork` (Ruling 46). Note that this whole block
            // — upgrade included — still waits behind the tracker/logs loads
            // above.
            async let engineRefresh: Void = Self.refreshEngineThenStartPeriodicRefresh(engineHealth) { [weak self] in
                await self?.runEngineUpgradeIfNeeded()
            }

            // Run environment check; publish result. It runs again when the
            // engine stops gating and after an upgrade (Ruling 69 I3).
            await self?.recheckActionItemsEnvironment()

            // Drives the window gate, Settings ▸ Engine, and the sidebar badge.
            await engineRefresh
        }

        startNotificationWatch()
    }

    /// First engine refresh, then the 10-minute re-check, then `next` (the
    /// launch-time upgrade) — chained on their own so nothing else in the
    /// launch task can delay them.
    private static func refreshEngineThenStartPeriodicRefresh(
        _ engineHealth: EngineHealthService, then next: @escaping @MainActor () async -> Void = {}
    ) async {
        await engineHealth.refresh()
        engineHealth.startPeriodicRefresh()
        await next()
    }

    // MARK: - Engine install, onboarding, upgrade (spec §5)

    /// Spec §5 "every launch": upgrade automatically only an app-managed
    /// engine whose vault is set up (otherwise onboarding finishes first)
    /// and that is either older than the bundled one, or AT the bundled
    /// version with an unfinished switch (Ruling 69 I1: a failure after
    /// `bootstrap upgrade` rewrote the pointer).
    nonisolated static func shouldAutoUpgrade(state: EngineState, release: EngineRelease?, switchUnfinished: Bool = false) -> Bool {
        guard let release, case .managed(let install, vaultBootstrapped: true) = state else { return false }
        if EngineUpgrader.needsUpgrade(state: state, bundledVersion: release.engine.version) { return true }
        guard switchUnfinished, let installed = install.version.flatMap(EngineVersion.init),
              let bundled = EngineVersion(release.engine.version) else { return false }
        return installed == bundled
    }

    /// True when installing the bundled engine would move an install
    /// backwards (the app is older than the engine) — never done.
    nonisolated static func wouldDowngrade(installed: String?, bundled: String) -> Bool {
        guard let i = installed.flatMap(EngineVersion.init), let b = EngineVersion(bundled) else { return false }
        return b < i
    }

    /// Ruling 69 I3: the scoutctl every service shells out to. An engine the
    /// app manages (or will install) is reached through the shim
    /// (`~/.local/bin/scoutctl`), which `bootstrap` renders on install and
    /// re-renders on every upgrade — so services wired at launch follow the
    /// switch. An external engine keeps the scoutctl it was found at (Part B).
    nonisolated static func serviceScoutctl(for state: EngineState, layout: EngineLayout) -> URL {
        if case .external(let install, _) = state { return install.scoutctl }
        return layout.shimURL
    }

    /// Probes `scoutctl action-items --help` and publishes the result to the
    /// Action Items banner.
    func recheckActionItemsEnvironment() async {
        let check = ActionItemsEnvironmentCheck(scoutctl: scoutctlExecutable, argumentsPrefix: scoutctlArgumentsPrefix, runner: runner)
        if let result = try? await check.run() { actionItemsEnvState.result = result }
    }

    /// See the `$lastChecked` subscription: only a failing check reruns.
    private func recheckActionItemsEnvironmentIfFailing() {
        guard !actionItemsEnvState.result.ok else { return }
        Task { [weak self] in await self?.recheckActionItemsEnvironment() }
    }

    private func engineStateDidChange() {
        refreshEngineSwitchState()
        syncOnboarding()
        let gates = engineHealth.state.gatesTabs
        let becameUsable = lastGatesTabs && !gates
        lastGatesTabs = gates
        if becameUsable { Task { [weak self] in await self?.recheckActionItemsEnvironment() } }
    }

    private func refreshEngineSwitchState() {
        let unfinished = engineRelease.map { EngineUpgrader(layout: engineLayout, release: $0).hasUnfinishedSwitch(state: engineHealth.state) } ?? false
        if unfinished != engineSwitchUnfinished { engineSwitchUnfinished = unfinished }
    }

    /// nil when this build carries no engine or Claude Code can't be found.
    /// Async because finding `claude` can fall back to a login shell, which
    /// must never block the main actor — it runs detached.
    func makeInstaller(progress: @escaping @Sendable (InstallProgress) -> Void) async -> EngineInstaller? {
        guard let engineRelease, let claude = await findClaude() else { return nil }
        return EngineInstaller(
            layout: engineLayout, release: engineRelease, tarballURL: engineTarballURL, runner: runner,
            uv: UvInstaller(release: engineRelease.uv, layout: engineLayout, downloader: fileDownloader, runner: runner),
            claude: URL(fileURLWithPath: claude), progress: progress)
    }

    /// The `claude` the installer would use (Settings ▸ Claude Code's path
    /// first), found off the main actor.
    private func findClaude() async -> String? {
        let override = defaults.string(forKey: "claudeCLIPath") ?? ""
        let resolve = resolveClaude
        return await Task.detached(operation: { resolve(override) }).value
    }

    /// A fresh onboarding flow for the current engine state. `appVault` is
    /// the vault this process's services were wired to, so Ready can say
    /// when a different choice needs a restart (Ruling 46). `onFinished`
    /// runs after the engine has been re-checked.
    func makeOnboardingModel(onFinished: @escaping @MainActor () -> Void) -> OnboardingViewModel {
        OnboardingViewModel(
            engineState: engineHealth.state, layout: engineLayout, release: engineRelease, runner: runner,
            prerequisites: PrerequisiteChecker(runner: runner, layout: engineLayout,
                                               claudePathOverride: defaults.string(forKey: "claudeCLIPath") ?? "",
                                               resolveClaude: resolveClaude),
            makeInstaller: { [weak self] sink in await self?.makeInstaller(progress: sink) },
            appVault: scoutDirectory,
            authorDefaults: defaults,
            onFinished: { [weak self] in
                Task { @MainActor in
                    await self?.engineHealth.refresh()
                    onFinished()
                }
            })
    }

    /// Whether the window keeps an onboarding flow: while the engine gates
    /// the tabs, or while a finishing flow holds it (Ruling 41).
    nonisolated static func keepsOnboarding(state: EngineState, holdsWindow: Bool) -> Bool {
        state.gatesTabs || holdsWindow
    }

    /// Creates the onboarding flow when the engine starts gating the tabs
    /// and drops it when it stops — unless the user is finishing a setup
    /// that already changed the machine.
    func syncOnboarding() {
        let holds = onboarding?.holdsWindow ?? false
        if Self.keepsOnboarding(state: engineHealth.state, holdsWindow: holds) {
            if onboarding == nil { onboarding = newOnboardingFlow() }
        } else {
            onboarding = nil
        }
    }

    /// Settings ▸ Engine's "Set up…" / "Repair…": the flow already running,
    /// else a new one — or nil when the engine doesn't gate (Ruling 69 M4:
    /// there is nothing for onboarding to set up).
    @discardableResult
    func beginOnboarding() -> OnboardingViewModel? {
        syncOnboarding()
        return onboarding
    }

    /// A flow can stop holding the window without the engine state changing
    /// (e.g. Back from a finished vault step); re-sync whenever it changes.
    private func watchOnboarding() {
        onboardingWatch = onboarding?.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.syncOnboarding() }
    }

    private func newOnboardingFlow() -> OnboardingViewModel {
        makeOnboardingModel(onFinished: { [weak self] in
            guard let self else { return }
            self.onboarding = nil
            self.syncOnboarding()   // still gated after setup? start over honestly
        })
    }

    /// Spec §5 "every launch": a newer bundled engine is applied for
    /// app-managed installs. Called from the launch task only. A launch
    /// upgrade to this same target that last failed at or after
    /// `bootstrapVault` is not retried automatically (final review I2): the
    /// sheet would re-open on every launch for a failure that needs the
    /// user. Settings ▸ Engine's Finish update / Repair… row carries it.
    /// The same goes for a launch that couldn't find Claude Code (Raven-Scout/Scout#346),
    /// until Claude Code is found: then the launch upgrade runs again.
    func runEngineUpgradeIfNeeded() async {
        let unfinished = engineRelease.map { EngineUpgrader(layout: engineLayout, release: $0).hasUnfinishedSwitch(state: engineHealth.state) } ?? false
        guard let engineRelease, Self.shouldAutoUpgrade(state: engineHealth.state, release: engineRelease, switchUnfinished: unfinished) else { return }
        if let memo = EngineUpgradeFailureMemo.load(from: defaults), memo.suppressesAutoUpgrade(to: engineRelease.engine.version) {
            guard memo.reason == .claudeCodeMissing, await findClaude() != nil else { return }
            EngineUpgradeFailureMemo.clear(in: defaults)   // found now; this run records its own outcome
        }
        await runEngineUpgrade(automatic: true)
    }

    /// Installs the bundled engine over a managed, set-up one
    /// (EngineUpgrader's steps) with the upgrade sheet showing progress.
    /// Settings ▸ Engine's Update / Finish update / Repair and the sheet's
    /// Retry call this directly; every step is idempotent, so a retry
    /// resumes where the last run stopped. Old versions are garbage-
    /// collected only after a fully successful run. Never downgrades.
    ///
    /// `automatic` is the launch-time run (`runEngineUpgradeIfNeeded`): its
    /// late-step failure, or not finding Claude Code, is remembered so the
    /// next launch backs off. A
    /// manual run clears that memo before it starts; any success clears it.
    func runEngineUpgrade(automatic: Bool = false) async {
        guard !isUpgradingEngine else { return }
        guard let engineRelease, case .managed(let install, vaultBootstrapped: true) = engineHealth.state,
              !Self.wouldDowngrade(installed: install.version, bundled: engineRelease.engine.version) else {
            if engineUpgradeProgress != nil {
                engineUpgradeError = "The engine is no longer an app-managed, set-up install this app can update. Settings ▸ Engine shows its state."
            }
            return
        }
        if !automatic { EngineUpgradeFailureMemo.clear(in: defaults) }
        engineUpgradeIsRepair = install.version == engineRelease.engine.version
        isUpgradingEngine = true
        defer { isUpgradingEngine = false }
        upgradeGeneration += 1
        let generation = upgradeGeneration
        liveUpgradeGeneration = generation
        engineUpgradeError = nil
        engineUpgradeProgress = [:]

        let ledger = InstallProgressLedger()
        let installer = await makeInstaller { [weak self] p in
            ledger.record(p)
            guard let appState = self else { return }
            Task { @MainActor in appState.applyUpgradeProgress(ledger, step: p.step, generation: generation) }
        }
        guard let installer else {
            liveUpgradeGeneration = nil
            if automatic { EngineUpgradeFailureMemo.claudeCodeMissing(target: engineRelease.engine.version).save(to: defaults) }
            engineUpgradeError = "Claude Code wasn't found, so the engine can't be updated. Install Claude Code (or set its path in Settings ▸ Claude Code), then Retry."
            return
        }
        let ok = await installer.run(steps: EngineUpgrader.upgradeSteps, mode: .upgrade(vault: install.vault ?? scoutDirectory))
        // Settle from the ledger; later hops from this run are dropped.
        if liveUpgradeGeneration == generation { liveUpgradeGeneration = nil }
        engineUpgradeProgress = ledger.snapshot
        if ok {
            EngineUpgradeFailureMemo.clear(in: defaults)
            _ = try? EngineUpgrader(layout: engineLayout, release: engineRelease).garbageCollect(keeping: engineRelease.engine.version)
        } else {
            if automatic, let memo = EngineUpgradeFailureMemo.forFailure(target: engineRelease.engine.version, progress: ledger.snapshot) {
                memo.save(to: defaults)
            }
            if EngineUpgradeSheet.failure(progress: ledger.snapshot, error: nil) == nil {
                engineUpgradeError = "The update stopped before it finished."
            }
        }
        await engineHealth.refresh()
        refreshEngineSwitchState()
        if ok {
            engineUpgradeProgress = nil
            // Services reach the engine through the shim, which the upgrade
            // re-rendered; re-probe so the Action Items banner follows.
            await recheckActionItemsEnvironment()
        }   // on failure the sheet stays with the log + Retry
    }

    /// The sheet's "Later": hides a failed upgrade. Ignored while one runs.
    func dismissEngineUpgrade() {
        guard !isUpgradingEngine else { return }
        engineUpgradeProgress = nil
        engineUpgradeError = nil
    }

    private func applyUpgradeProgress(_ ledger: InstallProgressLedger, step: InstallStep, generation: Int) {
        guard generation == liveUpgradeGeneration, let latest = ledger.latest(step) else { return }
        engineUpgradeProgress?[step] = latest
    }

    // MARK: - Configuration

    /// Everything `AppState` reaches outside its own process. `production()`
    /// is what the app ships with; tests substitute a temp directory, a
    /// scripted process runner, and an inert event source.
    struct Configuration {
        /// Vault root. Every service path is derived from this.
        var scoutDirectory: URL
        /// How `scoutctl` and `git` shell-outs are executed.
        var runner: any ProcessRunner
        /// FSEvents source the document services subscribe to.
        var fileEvents: any FileSystemEventSource
        /// Where `scoutctl` lives and how to invoke it.
        var scoutctl: ScoutctlInvocation
        /// Backing store for the user's path-override settings.
        var defaults: UserDefaults
        /// Where Claude Code keeps this vault's session transcripts
        /// (`~/.claude/projects/<encoded vault path>` in production). Part of
        /// the configuration so a test graph never reads the real home.
        var claudeSessionsDirectory: URL
        /// Where `SessionLogService` memoises parsed log bodies
        /// (`~/Library/Caches/Scout/parse-cache/<bundle id>/<vault key>.json` in
        /// production, one per build and vault).
        /// Configured for the same reason as `claudeSessionsDirectory`: the
        /// service's own default is the per-user path, so a test graph that
        /// left this to the default rewrote the *running app's* cache with
        /// whatever its fixture vault contained.
        var parseCacheURL: URL?
        /// Where the app-managed engine lives on disk (spec §4.1). Real home
        /// in production; a temp directory in every test configuration so a
        /// test run can never locate (or doctor-check) the user's real engine.
        var engineLayout: EngineLayout
        /// The engine state `EngineLocator.locate()` found synchronously at
        /// configuration time — lets `EngineHealthService` (and the window
        /// gate it drives) be correct from the very first frame, before the
        /// async `refresh()` has had a chance to run.
        var initialEngineState: EngineState
        /// When false the initializer wires the object graph but starts no
        /// timers, watches, loads, or subprocesses.
        var startsBackgroundWork: Bool
        /// Directories whose changes rebuild the session index while the
        /// Sessions page is open (`SessionsRefresh.productionWatchRoots()` in
        /// production). Defaults to none, so a test graph never watches the
        /// real `~/.claude` or the desktop app's store.
        var agentSessionWatchRoots: [URL] = []
        /// Where the Sessions page's index comes from. nil is `scoutctl
        /// session index`, which reads the Mac's real `~/.claude` whatever
        /// vault the app points at; a `FileSessionIndexSource` reads a fixed
        /// file instead (tests, the big-vault fixture, a Debug build on a
        /// synthetic vault via the `sessionsIndexFile` default). This covers
        /// the Sessions page only: `claudeSessionsDirectory` (Action Items'
        /// Launch Claude) still reads `~/.claude/projects/<encoded vault>`.
        var agentSessionSource: (any SessionIndexSource)? = nil
        /// The engine this build ships (spec §5). The bundle's in production;
        /// nil in every test configuration unless a test passes one, so no
        /// test can install or upgrade an engine by accident (Ruling 46).
        var engineRelease: EngineRelease? = nil
        /// The bundled `scout-engine-<v>.tar.gz` beside `engineRelease`.
        var engineTarballURL: URL? = nil
        /// Finds the `claude` binary from the user's path override. Production
        /// probes the real candidates and the login shell
        /// (`ClaudeLauncher.resolveClaudePath`); the default finds nothing.
        var resolveClaude: @Sendable (String) -> String? = { _ in nil }
        /// Downloads uv when no copy exists yet (installer only).
        var fileDownloader: any FileDownloader = URLSessionDownloader()
        /// Where run notifications go; nil is `NotificationService`, the
        /// real Notification Center. Tests pass a fake.
        var notifications: (any RunNotificationPosting)? = nil
        /// Where the knowledge map keeps its last settled layout
        /// (`~/Library/Caches/Scout/kb-map/<bundle id>/<vault key>.json` in
        /// production). nil keeps it in memory only, the default for every
        /// test graph, for the same reason as `parseCacheURL`.
        var mapLayoutCacheURL: URL? = nil

        static func production() -> Configuration {
            let layout = EngineLayout.live
            let locator = EngineLocator(layout: layout)
            let state = locator.locate()
            let release = try? EngineRelease.load(bundle: .main)
            // Vault root precedence (spec §4.4): the `scoutDataDir` default
            // (tilde expanded) → the engine pointer's `vault` → `~/Scout`.
            let vault = AppState.resolveScoutDirectory(
                defaults: .standard, pointer: locator.pointer(), home: layout.home
            )
            // A Debug build pointed at a synthetic vault reads its sessions
            // from a fixture file and watches nothing under the real home. A
            // Release build ignores the default: it always asks scoutctl.
            #if DEBUG
            let sessionsFile = FileSessionIndexSource.configured(in: .standard)
            #else
            let sessionsFile: FileSessionIndexSource? = nil
            #endif
            var configuration = Configuration(
                scoutDirectory: vault,
                // Every scoutctl/git call must see the vault the app is
                // looking at (the engine defaults to ~/Scout otherwise) —
                // inject it once, here, so every call site gets it for free.
                runner: EnvironmentInjectingRunner(base: SystemProcessRunner(), extra: ["SCOUT_DATA_DIR": vault.path]),
                fileEvents: FileWatcher(),
                // The engine is found by EngineLocator (pointer → conventional
                // layout → shim → marketplace cache → dev checkout). An
                // external engine keeps the scoutctl it was found at; one the
                // app manages — or will install — is reached through the shim,
                // which bootstrap (re-)renders, so services follow onboarding
                // and upgrades without a restart (Ruling 69 I3). ENOENT at
                // the shim before setup is the honest failure, and
                // EngineHealthService gates the UI on the same fact.
                scoutctl: AppState.ScoutctlInvocation(executable: AppState.serviceScoutctl(for: state, layout: layout), argsPrefix: []),
                defaults: .standard,
                claudeSessionsDirectory: ClaudeSessionService
                    .defaultScoutSessionsDirectory(scoutDirectory: vault),
                parseCacheURL: SessionLogService.defaultParseCacheURL(scoutDirectory: vault),
                engineLayout: layout,
                initialEngineState: state,
                startsBackgroundWork: true,
                agentSessionWatchRoots: sessionsFile == nil ? SessionsRefresh.productionWatchRoots() : [],
                agentSessionSource: sessionsFile,
                engineRelease: release,
                engineTarballURL: release?.bundledTarballURL(bundle: .main),
                resolveClaude: PrerequisiteChecker.defaultResolveClaude,
                fileDownloader: URLSessionDownloader()
            )
            configuration.mapLayoutCacheURL = KBMapLayoutCache.defaultFile(scoutDirectory: vault)
            return configuration
        }

        /// What `ScoutApp` boots with. Under `xcodebuild test` this process is
        /// the ScoutTests host, so the graph is wired against a scratch
        /// directory with background work off — otherwise every test run
        /// watched `~/Scout` and polled `scoutctl` from the host, and the
        /// coverage gate measured that live graph.
        static func forCurrentProcess() -> Configuration {
            isTestHost ? testHost() : production()
        }

        static var isTestHost: Bool {
            let env = ProcessInfo.processInfo.environment
            return env["XCTestConfigurationFilePath"] != nil
                || env["XCTestBundlePath"] != nil
                || NSClassFromString("XCTestCase") != nil
        }

        /// Inert wiring for the test host, built from the real runner and
        /// watcher types so nothing test-only ships in the app. Nothing
        /// subscribes to the watcher because background work is off.
        static func testHost() -> Configuration {
            let dir = FileManager.default.temporaryDirectory
                .appendingPathComponent("scout-test-host", isDirectory: true)
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let engineHome = dir.appendingPathComponent("engine-home", isDirectory: true)
            let testInstall = EngineInstall(
                root: dir, scoutctl: URL(fileURLWithPath: "/usr/bin/false"),
                python: nil, version: nil, vault: dir
            )
            return Configuration(
                scoutDirectory: dir,
                runner: SystemProcessRunner(),
                fileEvents: FileWatcher(),
                scoutctl: AppState.ScoutctlInvocation(
                    executable: URL(fileURLWithPath: "/usr/bin/false"),
                    argsPrefix: []
                ),
                defaults: UserDefaults(suiteName: "scout.test-host") ?? .standard,
                claudeSessionsDirectory: dir.appendingPathComponent(".claude-projects"),
                parseCacheURL: dir.appendingPathComponent("session-parse-cache.json"),
                // Never the real home — this process is the ScoutTests host,
                // and a test run must never locate (or doctor-check) the
                // user's real engine.
                engineLayout: EngineLayout(home: engineHome),
                initialEngineState: .external(testInstall, .unknown("test-host")),
                startsBackgroundWork: false,
                // No bundled engine and no `claude`: the test host can never
                // install or upgrade anything (Ruling 46).
                engineRelease: nil,
                engineTarballURL: nil,
                resolveClaude: { _ in nil }
            )
        }
    }

    /// Switch to Control Center and open `id`'s run detail. (#43)
    func requestOpenRun(_ id: Run.ID) {
        pendingRunToOpen = id
        requestedSidebar = .controlCenter
    }

    /// Switch to the Knowledge Base and open the note `target` resolves to
    /// (an action item's entity chip, Raven-Scout/Scout#295).
    func requestOpenKBNote(_ target: String) {
        pendingKBNote = target
        requestedSidebar = .knowledgeBase
    }

    /// Shells out to `scoutctl schedule fire-now <slotKey>`.
    ///
    /// Plan 5 removed in-app dispatch — the engine now owns slot routing,
    /// scout-app just shells out. A failure lands in `fireNowError`.
    ///
    /// There is no budget override: `fire-now` has no such option, and the
    /// runner's budget check applies to every run.
    ///
    /// After the dispatch returns, immediately refresh `ScheduleService` so
    /// the heartbeat strip drops the just-fired slot instead of sitting on
    /// the past `scheduled_at` until the next 60 s poll tick.
    ///
    /// Returns `fireNowError` as this call left it: nil when the run started.
    @discardableResult
    func fireNow(slotKey: String) async -> String? {
        guard firingSlotKeys.insert(slotKey).inserted else {
            // Surface the drop: several UI surfaces can fire the same slot
            // (upcoming strip, RunDetailView's Run again, menu panel), and a
            // silently discarded request looks like it was dispatched.
            fireNowError = "\(slotKey) is already being started — request ignored."
            return fireNowError
        }
        defer { firingSlotKeys.remove(slotKey) }
        let args = Self.fireNowArguments(argumentsPrefix: scoutctlArgumentsPrefix, slotKey: slotKey)
        var error: String?
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
                error = "Run now failed (exit \(result.exitCode))"
                    + (detail.isEmpty ? "" : ": \(detail)")
            }
        } catch let launch {
            error = "Run now failed: \(launch.localizedDescription)"
        }
        fireNowError = error
        await scheduleService.refresh()
        return error
    }

    /// What the last "Install wake schedule" did, shown in the menu-bar panel
    /// until dismissed: `.info` when it worked. It used to run under `try?`
    /// and report nothing either way (UI audit #4).
    @Published var wakeScheduleResult: WakeScheduleResult? = nil
    @Published private(set) var installingWakeSchedule = false

    /// What "Install wake schedule" did, and the command to run by hand when
    /// macOS needs an administrator (shown on its own line, with Copy).
    nonisolated struct WakeScheduleResult: Equatable, Sendable {
        var problem: Problem
        /// `sudo pmset repeat wakeorpoweron MTWRF 07:55:00`; nil unless the
        /// install was refused and the dry run named the command.
        var sudoCommand: String? = nil
    }

    /// `scoutctl schedule install-wake-schedule`: a `pmset repeat` rule that
    /// wakes the Mac at the earliest weekday run's time, on that run's days.
    /// A dry run first names the exact `pmset` command, so when macOS refuses
    /// it (it needs an administrator) the panel can say what to run instead;
    /// after a clean exit `pmset -g sched` (read-only) confirms the rule is
    /// there before the panel says "installed".
    ///
    /// `checkLimit` is how long that check may take before the panel says
    /// Scout couldn't check: `SystemProcessRunner` has no timeout of its own.
    func installWakeSchedule(checkLimit: Duration = .seconds(5)) async {
        guard !installingWakeSchedule else { return }
        installingWakeSchedule = true
        defer { installingWakeSchedule = false }
        let base = scoutctlArgumentsPrefix + ["schedule", "install-wake-schedule"]
        func run(_ extra: [String]) async throws -> ProcessResult {
            try await runner.run(executable: scoutctlExecutable, arguments: base + extra,
                                 environment: [:], workingDirectory: scoutDirectory)
        }
        let dryRun = try? await run(["--dry-run"])
        do {
            let result = try await run([])
            var check: ProcessResult?
            if result.exitCode == 0 {
                let runner = self.runner
                check = await Self.withinTimeLimit(checkLimit) {
                    try? await runner.run(executable: URL(fileURLWithPath: "/usr/bin/pmset"),
                                          arguments: ["-g", "sched"], environment: [:], workingDirectory: nil)
                }
            }
            wakeScheduleResult = Self.wakeScheduleResult(result, dryRun: dryRun, check: check)
        } catch {
            wakeScheduleResult = Self.wakeScheduleResult(nil, dryRun: dryRun, check: nil,
                                                         launchError: error.localizedDescription)
        }
    }

    /// `operation`'s result, or nil once `limit` passes first. The operation
    /// is cancelled then; a child process it started that ignores that is
    /// left to finish on its own, but nothing waits for it.
    ///
    /// Both tasks are detached: under approachable concurrency this function
    /// runs on its caller's actor, and the timer must not need that actor
    /// (the main one, for `installWakeSchedule`) free to fire. The caller
    /// itself still resumes only once its actor is free.
    nonisolated static func withinTimeLimit<T: Sendable>(
        _ limit: Duration, _ operation: @escaping @Sendable () async -> T?
    ) async -> T? {
        let settled = OSAllocatedUnfairLock(initialState: false)
        @Sendable func first() -> Bool {
            settled.withLock { done in
                defer { done = true }
                return !done
            }
        }
        return await withCheckedContinuation { (cont: CheckedContinuation<T?, Never>) in
            let work = Task.detached {
                let value = await operation()
                if first() { cont.resume(returning: value) }
            }
            Task.detached {
                try? await Task.sleep(for: limit)
                if first() {
                    work.cancel()
                    cont.resume(returning: nil)
                }
            }
        }
    }

    /// The `pmset` command a dry run names: "[dry-run] would run: pmset …". Pure.
    nonisolated static func wakeCommand(fromDryRun dryRun: ProcessResult?) -> String? {
        guard let dryRun, dryRun.exitCode == 0,
              let out = String(data: dryRun.stdout, encoding: .utf8) else { return nil }
        let marker = "would run: "
        return out.components(separatedBy: "\n")
            .first { $0.contains(marker) }
            .map { String($0[$0.range(of: marker)!.upperBound...]).trimmingCharacters(in: .whitespaces) }
            .flatMap { $0.hasPrefix("pmset ") ? $0 : nil }
    }

    /// Whether `pmset -g sched` lists the repeating wake `command` sets:
    /// "wakepoweron at 7:55AM" for `… wakeorpoweron MTWRF 07:55:00`. Without
    /// a command (or one with no time in it) nothing counts: a wake set
    /// earlier, by Scout or anyone else, proves nothing about this one. Pure.
    nonisolated static func wakeScheduleConfirmed(sched: String, command: String?) -> Bool {
        let lines = sched.components(separatedBy: "\n")
        guard let start = lines.firstIndex(where: { $0.hasPrefix("Repeating power events") }) else { return false }
        let repeating = lines[(start + 1)...].prefix { $0.hasPrefix(" ") }.map { $0.lowercased() }
        let wakes = repeating.filter { $0.contains("wake") }
        guard let time = command?.split(separator: " ").last.map(String.init) else { return false }
        let parts = time.split(separator: ":").compactMap { Int($0) }
        guard parts.count >= 2 else { return false }
        let hour12 = parts[0] % 12 == 0 ? 12 : parts[0] % 12
        let label = String(format: "%d:%02d%@", hour12, parts[1], parts[0] < 12 ? "am" : "pm")
        return wakes.contains { $0.contains("at \(label)") }
    }

    /// The panel's words for an install-wake-schedule result. Pure.
    nonisolated static func wakeScheduleResult(_ result: ProcessResult?, dryRun: ProcessResult?,
                                               check: ProcessResult?, launchError: String? = nil) -> WakeScheduleResult {
        func text(_ data: Data?) -> String {
            data.flatMap { String(data: $0, encoding: .utf8) }?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        }
        let out = text(result?.stdout), err = text(result?.stderr)
        let command = wakeCommand(fromDryRun: dryRun)
        let details = [out, err, launchError].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: "\n")
        if let result, result.exitCode == 0 {
            // nil `check`: `pmset -g sched` didn't start, or ran out of time.
            let checked = check?.exitCode == 0
            let sched = text(checked ? check?.stdout : nil)
            guard checked, wakeScheduleConfirmed(sched: sched, command: command) else {
                let impact = !checked
                    ? "Scout set it, but couldn't check macOS's list of this Mac's repeating wake times."
                    : command == nil
                        ? "Scout set it, but couldn't tell which wake time to look for in macOS's list."
                        : "Scout set it, but macOS doesn't list it among this Mac's repeating wake times."
                return WakeScheduleResult(problem: Problem(
                    severity: .warning, title: "Couldn't confirm the wake schedule",
                    impact: impact,
                    fix: "Check with `pmset -g sched` in Terminal, or try again.",
                    details: [details, sched.isEmpty ? nil : "pmset -g sched:\n\(sched)"]
                        .compactMap { $0 }.joined(separator: "\n")))
            }
            return WakeScheduleResult(problem: Problem(
                severity: .info, title: "Wake schedule installed",
                impact: "Your Mac wakes at the time of the earliest weekday run, on that run's days, while it's plugged in. It replaced any repeating wake time set before.",
                details: details))
        }
        if (err + out).contains("no weekday slot") {
            return WakeScheduleResult(problem: Problem(
                severity: .warning, title: "Nothing to wake for",
                impact: "No schedule runs on a weekday, so there's no time to wake the Mac.",
                fix: "Add a weekday time on the Schedules page, then try again.", details: details))
        }
        let fix = command == nil
            ? "macOS needs an administrator for this. Copy the details when asking for help."
            : "macOS needs an administrator for this. Run this command in Terminal. It replaces any repeating wake time already set on this Mac."
        return WakeScheduleResult(
            problem: Problem(severity: .error, title: "Couldn't install the wake schedule",
                             impact: "Scheduled runs only start while the Mac is awake.",
                             fix: fix, details: details),
            sudoCommand: command.map { "sudo \($0)" })
    }

    /// Recompute the menu-bar urgent badge for today.
    ///
    /// Runs at launch and on every menu-bar open, and used to read and parse
    /// the whole day synchronously here — the one main-actor parse #103 left
    /// behind, on a file that is routinely 1.8 MB.
    ///
    /// Two paths now. When the document service already holds today's
    /// document, that *is* the answer: it watches the file and republishes on
    /// every change, and reusing it keeps the badge and the Action Items list
    /// from disagreeing inside the watcher's debounce window. Otherwise the
    /// read and parse happen off the main actor, with the same byline the
    /// service would have used rather than the parser's `"user"` default.
    func refreshUrgentActionCount() async {
        let today = ActionItemsDay.today()
        if case .loaded(let document) = actionItemsDocumentService.state,
           ActionItemsDay.stem(for: document.date) == ActionItemsDay.stem(for: today) {
            urgentActionCount = Self.urgentOpenCount(in: document)
            return
        }

        let url = actionItemsDocumentService.url(for: today)
        let byline = ActionItemsDocumentService.inlineCommentAuthor(from: defaults)
        let document = await Task.detached(priority: .utility) { () -> ActionItemsDocument? in
            guard let data = try? Data(contentsOf: url),
                  let text = String(data: data, encoding: .utf8) else { return nil }
            return try? ActionItemsParser.parse(
                text: text,
                sourceURL: url,
                sourceBytes: data.count,
                inlineCommentAuthor: byline
            )
        }.value

        urgentActionCount = document.map(Self.urgentOpenCount(in:)) ?? 0
    }

    nonisolated static func urgentOpenCount(in document: ActionItemsDocument) -> Int {
        document.sections.reduce(into: 0) { count, section in
            count += section.tasks.filter { task in
                !task.done
                    && task.snoozedUntil == nil
                    && (task.snoozedFromKind ?? section.kind) == .urgent
            }.count
        }
    }

    /// Build the argv for `scoutctl schedule fire-now`. argv[0] must be the
    /// resolved `argumentsPrefix` (always empty now: scoutctl is an absolute
    /// path, with no `/usr/bin/env` fallback) — never a hardcoded
    /// "scoutctl", which an absolute-path executable would receive as a bogus
    /// subcommand (issue #45).
    nonisolated static func fireNowArguments(argumentsPrefix: [String], slotKey: String) -> [String] {
        argumentsPrefix + ["schedule", "fire-now", slotKey]
    }

    /// Where scoutctl lives + how to invoke it. Used by the constructor to
    /// wire ScheduleService and ScheduleEditService at startup.
    struct ScoutctlInvocation {
        /// The resolved `scoutctl` executable — found by `EngineLocator` in
        /// `Configuration.production()`, or the shim path as an honest ENOENT
        /// when no engine is found. Always an absolute path; there is no
        /// `/usr/bin/env` + PATH fallback.
        let executable: URL
        /// Args inserted before the user's args. Always empty now that
        /// `executable` is always a concrete path, never `/usr/bin/env`.
        let argsPrefix: [String]
    }

    /// Vault root precedence (spec §4.4): the `scoutDataDir` default (tilde
    /// expanded) → the engine pointer's `vault` → `~/Scout`.
    ///
    /// Tilde expansion is done against the `home` parameter, not
    /// `NSString.expandingTildeInPath` (which always expands against the
    /// real process home) — `home` is itself the real home in production,
    /// but a test that injects a fixture `home` needs `~` to expand against
    /// *that*, not the host machine's actual home directory.
    nonisolated static func resolveScoutDirectory(defaults: UserDefaults, pointer: EnginePointer?, home: URL) -> URL {
        if let raw = defaults.string(forKey: "scoutDataDir")?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty {
            if raw == "~" { return home }
            if raw.hasPrefix("~/") { return home.appendingPathComponent(String(raw.dropFirst(2))) }
            if raw.hasPrefix("/") { return URL(fileURLWithPath: raw) }
            // Anything else (`Scout`, `Vaults/Work`, `~alex/Scout`) would
            // resolve against the process's cwd — treat it as unset and fall
            // through, the same way a relative pointer `vault` is rejected below.
        }
        // A blank or relative `vault` in the pointer (hand-edited, truncated
        // write, or a schema we don't fully trust) is not a usable root —
        // treat it the same as "no pointer" rather than resolving a bogus
        // relative URL against the process's cwd.
        if let pointer, pointer.vault.hasPrefix("/") { return URL(fileURLWithPath: pointer.vault) }
        return home.appendingPathComponent("Scout")
    }

    /// Re-derive `status` from the engine's health and the latest run.
    func recomputeStatus() {
        let next = ScoutStatus.derive(engine: engineHealth.state, doctor: engineHealth.doctor,
                                      doctorError: engineHealth.lastError,
                                      latestRun: sessionLogService.runs.first)
        if next != status { status = next }
    }

    /// Open a run in the Control Center, from a notification click: front
    /// the main window and let `ControlCenterView` open the run's detail.
    func openRun(id: Run.ID) {
        requestedSidebar = .controlCenter
        pendingRunToOpen = id
        NSApp.activate(ignoringOtherApps: true)
        if let openMainWindow {
            openMainWindow()
        } else if let window = NSApp.windows.first(where: { $0.identifier?.rawValue.hasPrefix("main") == true }) {
            // No SwiftUI view has handed over its opener yet (the window
            // started hidden and the panel was never opened). The run still
            // opens the next time the window shows: `pendingRunToOpen` waits.
            window.makeKeyAndOrderFront(nil)
        }
    }

    /// The SwiftUI `openWindow(id: "main")` action, handed over by the first
    /// view that has one (the main window or the menu-bar panel), so a
    /// notification click can reopen a closed window.
    var openMainWindow: (() -> Void)?

    /// False until the launch-time log load finishes, so runs already on
    /// disk never notify.
    private var runNotificationsArmed = false

    /// A notification's "Run again": fire the schedule the failed run ran for,
    /// as Run again in its detail does. The notification only offers it for a
    /// run with a schedule of its own (`NotificationService.retrySlotKey`).
    func retryFromNotification(slotKey: String) {
        Task { await runAgainFromNotification(slotKey: slotKey) }
    }

    /// `retryFromNotification`'s work. The owner pressed it outside the app,
    /// where `fireNowError` shows nowhere, so a refusal (an unknown schedule,
    /// another run starting at that moment) comes back as a notification too.
    func runAgainFromNotification(slotKey: String) async {
        if let error = await fireNow(slotKey: slotKey) {
            notificationService.postRunAgainRefused(slotKey: slotKey, error: error)
        }
    }

    private func startNotificationWatch() {
        sessionLogService.$runs.sink { [weak self] runs in
            guard let self else { return }
            Task { @MainActor in
                let defaults = UserDefaults.standard
                let prefs = RunNotificationPolicy.Preferences(
                    onFailure: defaults.object(forKey: RunNotificationPolicy.failureKey) as? Bool ?? true,
                    onRateLimit: defaults.object(forKey: RunNotificationPolicy.rateLimitKey) as? Bool ?? true)
                self.runNotifications.update(runs: runs, armed: self.runNotificationsArmed, preferences: prefs)
                self.recomputeStatus()
            }
        }.store(in: &cancellables)
    }
}
