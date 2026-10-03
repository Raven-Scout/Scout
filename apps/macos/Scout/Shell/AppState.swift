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
    @Published private(set) var firingSlotKeys: Set<String> = []
    @Published private(set) var urgentActionCount: Int = 0

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

        // Everything below spawns work that outlives the initializer — polling
        // timers, FSEvents subscriptions, launch-time loads and a `scoutctl`
        // shell-out. Tests build the same object graph with this switched off
        // so a rendered view can't reach the filesystem or the network.
        guard configuration.startsBackgroundWork else { return }

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
            // Re-checking every 10 minutes (spec §4.4) still starts only
            // after this first refresh completes, below.
            async let engineRefresh: Void = engineHealth.refresh()

            // Run environment check; publish result.
            let check = ActionItemsEnvironmentCheck(
                scoutctl: scoutctlExe,
                argumentsPrefix: scoutctlArgsPrefix,
                runner: runner
            )
            if let result = try? await check.run() {
                await MainActor.run { envState.result = result }
            }

            // Drives the window gate, Settings ▸ Engine, and the sidebar badge.
            await engineRefresh
            await MainActor.run { engineHealth.startPeriodicRefresh() }
        }

        startNotificationWatch()
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

        static func production() -> Configuration {
            let layout = EngineLayout.live
            let locator = EngineLocator(layout: layout)
            let state = locator.locate()
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
                // layout → shim → marketplace cache → dev checkout). When
                // nothing is found we fall back to the shim path: ENOENT
                // there is the honest failure, and EngineHealthService gates
                // the UI on the same fact. No more `/usr/bin/env scoutctl`.
                scoutctl: AppState.ScoutctlInvocation(executable: state.scoutctl ?? layout.shimURL, argsPrefix: []),
                defaults: .standard,
                claudeSessionsDirectory: ClaudeSessionService
                    .defaultScoutSessionsDirectory(scoutDirectory: vault),
                parseCacheURL: SessionLogService.defaultParseCacheURL(),
                engineLayout: layout,
                initialEngineState: state,
                startsBackgroundWork: true
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
                startsBackgroundWork: false
            )
        }
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
            return URL(fileURLWithPath: raw)
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
