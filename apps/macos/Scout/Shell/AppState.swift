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

    /// A run another tab asked to open (the Wishlist/Research history pane).
    /// ControlCenterView opens its detail and clears this. (#43)
    @Published var pendingRunToOpen: Run.ID? = nil

    /// A sidebar tab another view asked to switch to. MainWindowView applies
    /// it to its selection and clears it. (#43)
    @Published var requestedSidebar: SidebarItem? = nil
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
    let sessionTokensService: SessionTokensService
    let connectorHealthService: ConnectorHealthService
    let sessionLogService: SessionLogService
    let scheduleService: ScheduleService
    let powerStateService: PowerStateService
    let scheduleEditService: ScheduleEditService
    let budgetSettingsService: BudgetSettingsService
    let gitService: GitService
    let notificationService: NotificationService
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

    private var previousStatus: [Run.ID: RunStatus] = [:]
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

        let git = GitService(repoURL: scoutDir, runner: runner)
        let tracker = UsageTrackerService(
            trackerURL: scoutDir.appendingPathComponent(".scout-logs/usage-tracker.jsonl"),
            fileEvents: events
        )
        let tokens = SessionTokensService(
            trackerURL: scoutDir.appendingPathComponent(".scout-logs/session-tokens.jsonl"),
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
            argumentsPrefix: scoutctlArgsPrefix
        )
        // Budget config is read and written through `scoutctl budget show/set`,
        // never by parsing scout-config.yaml here — that file doubles as
        // bootstrap state with several producers.
        let budgetSettings = BudgetSettingsService(
            scoutctl: scoutctlExe,
            runner: runner,
            argumentsPrefix: scoutctlArgsPrefix
        )
        let notif = NotificationService()
        let ccSessions = ClaudeSessionService(
            projectsDirectory: configuration.claudeSessionsDirectory
        )
        let sessionIndex = SessionIndexService(configuration: .init(
            scoutctl: scoutctlExe,
            argumentsPrefix: scoutctlArgsPrefix,
            runner: runner,
            fileEvents: events,
            indexFile: scoutDir.appendingPathComponent(".scout-cache/sessions-index.json"),
            watchRoots: configuration.agentSessionWatchRoots
        ))

        let docService = ActionItemsDocumentService(
            directory: actionItemsDir, fileEvents: events, defaults: defaults
        )
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

        self.fileEvents = events
        self.gitService = git
        self.trackerService = tracker
        self.sessionTokensService = tokens
        self.connectorHealthService = connectorHealth
        self.sessionLogService = logs
        self.scheduleService = sched
        self.powerStateService = power
        self.scheduleEditService = scheduleEditService
        self.budgetSettingsService = budgetSettings
        self.notificationService = notif
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
        self.scoutDirectory = scoutDir
        self.actionItemsDirectory = actionItemsDir
        self.defaults = defaults
        self.runner = runner
        self.scoutctlExecutable = scoutctlExe
        self.scoutctlArgumentsPrefix = scoutctlArgsPrefix
        self.engineLayout = configuration.engineLayout
        self.engineHealth = engineHealth
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
        // The onboarding gate, the unfinished-switch flag and the Action
        // Items check follow the engine state. Every refresh publishes (no
        // de-duplication): the switch flag reads the disk, which can change
        // while the state value doesn't.
        engineHealth.$state
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.engineStateDidChange() }
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

        // Everything below spawns work that outlives the initializer — polling
        // timers, FSEvents subscriptions, launch-time loads and a `scoutctl`
        // shell-out. Tests build the same object graph with this switched off
        // so a rendered view can't reach the filesystem or the network.
        guard configuration.startsBackgroundWork else { return }

        // Loads the last index from disk, then keeps it current: the sidebar
        // badge needs it before the Sessions page is ever opened.
        sessionIndex.start()

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
            }
            await self?.recomputeMenuStatus()
            await self?.refreshUrgentActionCount()

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
        guard let engineRelease else { return nil }
        let override = defaults.string(forKey: "claudeCLIPath") ?? ""
        let resolve = resolveClaude
        guard let claude = await Task.detached(operation: { resolve(override) }).value else { return nil }
        return EngineInstaller(
            layout: engineLayout, release: engineRelease, tarballURL: engineTarballURL, runner: runner,
            uv: UvInstaller(release: engineRelease.uv, layout: engineLayout, downloader: fileDownloader, runner: runner),
            claude: URL(fileURLWithPath: claude), progress: progress)
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
    func runEngineUpgradeIfNeeded() async {
        let unfinished = engineRelease.map { EngineUpgrader(layout: engineLayout, release: $0).hasUnfinishedSwitch(state: engineHealth.state) } ?? false
        guard let engineRelease, Self.shouldAutoUpgrade(state: engineHealth.state, release: engineRelease, switchUnfinished: unfinished) else { return }
        if EngineUpgradeFailureMemo.load(from: defaults)?.suppressesAutoUpgrade(to: engineRelease.engine.version) == true { return }
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
    /// late-step failure is remembered so the next launch backs off. A
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
        /// (`~/Library/Caches/Scout/session-parse-cache.json` in production).
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
            return Configuration(
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
                parseCacheURL: SessionLogService.defaultParseCacheURL(),
                engineLayout: layout,
                initialEngineState: state,
                startsBackgroundWork: true,
                agentSessionWatchRoots: SessionsRefresh.productionWatchRoots(),
                engineRelease: release,
                engineTarballURL: release?.bundledTarballURL(bundle: .main),
                resolveClaude: PrerequisiteChecker.defaultResolveClaude,
                fileDownloader: URLSessionDownloader()
            )
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
        guard firingSlotKeys.insert(slotKey).inserted else {
            // Surface the drop: several UI surfaces can fire the same slot
            // (upcoming strip, RunDetailView's bypass retry, menu panel) and
            // only the menu panel disables on firingSlotKeys — a silently
            // discarded bypass-budget retry looks like it was dispatched.
            fireNowError = "\(slotKey) is already being started — request ignored."
            return
        }
        defer { firingSlotKeys.remove(slotKey) }
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
