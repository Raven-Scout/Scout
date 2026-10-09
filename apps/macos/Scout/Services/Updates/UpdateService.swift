import Foundation
import Combine

/// Which of the two independent things can be behind.
enum UpdateTrack: CaseIterable, Sendable { case app, plugin }

/// One track's view of the world. `currentVersion` is what's installed /
/// running; `latestVersion` is nil until a check has answered.
struct UpdateStatus: Equatable, Sendable {
    enum State: Equatable, Sendable {
        case idle, checking, upToDate, available
        case error(String)
    }
    var currentVersion: String?
    var latestVersion: String?
    var state: State = .idle

    var isAvailable: Bool { state == .available }
}

/// What the Sparkle adapter reports back. Kept Sparkle-free so the service
/// (and its tests) never import Sparkle.
enum AppUpdateEvent: Equatable, Sendable {
    case found(version: String)
    case upToDate
    /// The update cycle ended with nothing more to report: no update found
    /// (already covered by `.upToDate` separately), a user cancel, or an
    /// "install later" authorization deferral. Sparkle calls
    /// `didFinishUpdateCycleForUpdateCheck:error:` for every completed
    /// cycle — including a silent cancel, which never calls
    /// `didAbortWithError:` because its error is nil — so this is the only
    /// reliable terminal signal for a check that didn't find (or install)
    /// anything.
    case cycleEnded
    case failed(String)
}

/// The app-track controller the service drives. `AppUpdater` (Sparkle) is
/// the production implementation; tests use a fake.
@MainActor
protocol AppUpdateController: AnyObject {
    /// False in Debug builds — the updater never starts there.
    var isEnabled: Bool { get }
    var currentVersion: String? { get }
    /// User-initiated check. Returns whether a check actually started —
    /// false when disabled, or when Sparkle reports a session is already in
    /// progress or a permission prompt is showing (in which case it has
    /// already silently declined and calls back with nothing). Results come
    /// back through `AppUpdateEvent`s.
    @discardableResult
    func checkForUpdates() -> Bool
}

/// Outcome of one plugin check. `applicable == false` means the engine state
/// the check ran against has nothing for the plugin row to show (a managed
/// engine, no engine at all, or a broken one) — the UI hides the row; it is
/// not an error. `installed == nil` (while `applicable`) means "no scout
/// plugin found under this external engine" — also not an error.
struct PluginUpdateResult: Equatable, Sendable {
    var applicable: Bool
    var installed: String?
    var latest: String?
    var isUpdateAvailable: Bool
    var releasesURL: URL?
    var error: String?

    static let notApplicable = PluginUpdateResult(
        applicable: false, installed: nil, latest: nil, isUpdateAvailable: false, releasesURL: nil, error: nil
    )
}

protocol PluginUpdateChecking: Sendable {
    func check(engine: EngineState) async -> PluginUpdateResult
}

/// One observable for both tracks; drives Settings ▸ Updates and the badges.
///
/// App track: Sparkle owns detection *and* installation. We only mirror what
/// it tells us (via `applyAppEvent`) and forward the user's "check now".
/// Plugin track: detect + hand off. The app cannot apply a plugin update —
/// that happens inside Claude Code (`/scout-update`) — so the primary action
/// is copying the command. The plugin row only makes sense while the engine
/// is externally managed (devs, install.sh, Claude Code, the marketplace
/// cache) — a Scout-managed engine updates itself, and there is nothing to
/// report with no engine at all. We follow `engineStates` (EngineHealthService
/// `.$state`) and recheck whenever the eligible install changes.
@MainActor
final class UpdateService: ObservableObject {
    static let pluginUpdateCommand = "/scout-update"

    @Published private(set) var appUpdate: UpdateStatus
    /// `.checking` is set before applicability is known (see `checkPlugin()`),
    /// so a stale/hidden check can still be mid-flight here. UI must gate on
    /// `pluginRowVisible` before reading this, not the other way around.
    @Published private(set) var pluginUpdate = UpdateStatus()
    @Published private(set) var pluginRowVisible = false
    @Published private(set) var pluginReleasesURL: URL?

    private var appController: (any AppUpdateController)?
    private let pluginChecker: any PluginUpdateChecking
    private var cancellables: Set<AnyCancellable> = []

    /// `appUpdate.state` as it was just before the most recent `checkApp()`
    /// actually started a check, so `.cycleEnded` (a silent cancel or
    /// "install later" with no other event) can restore it instead of
    /// leaving the row stuck on `.checking`. Nil means "no check in flight
    /// from this state" — `.cycleEnded` then falls back to `.idle`.
    private var appStateBeforeChecking: UpdateStatus.State?

    /// The most recent engine state received from `engineStates`, nil until
    /// the first one arrives. `checkPlugin()` uses this; with none received
    /// yet, it has nothing to check against and is a no-op.
    private var latestEngineState: EngineState?

    /// Dedup key for the current engine state (nil for states the plugin row
    /// doesn't apply to), compared against the previous emission so the
    /// 10-minute engine re-check and doctor refreshes don't refetch the
    /// plugin when nothing eligibility-relevant changed. `hasEligibilityKey`
    /// distinguishes "no engine state observed yet" from "observed one whose
    /// key happens to be nil" — both must still trigger a check on first sight.
    private var hasEligibilityKey = false
    private var lastEligibilityKey: String?

    /// The in-flight (or most recently completed) plugin check, exposed so
    /// tests can `await service.pluginTask?.value` instead of sleeping.
    internal private(set) var pluginTask: Task<Void, Never>?

    /// Bumped at the entry of every `checkPlugin()` call, mirroring
    /// `EngineHealthService.generation`. `pluginTask?.cancel()` in `check(_:)`
    /// only sets the task's cancellation flag — it doesn't stop a plugin
    /// checker that's already mid-await and non-throwing, so a superseded
    /// check can still resolve after a newer one. Gating `applyPluginResult`
    /// on "am I still the latest call" (rather than relying on cancellation)
    /// is what actually prevents a late, stale result from overwriting the
    /// current one.
    private var pluginCheckGeneration = 0

    /// - Parameters:
    ///   - pluginChecker: the plugin-track checker (file reads + one HTTPS GET).
    ///   - engineStates: `EngineHealthService.$state`. A `@Published`
    ///     publisher replays its current value on subscribe, which is what
    ///     drives the plugin track's launch check — there is no separate
    ///     `startLaunchChecks()` to call.
    ///   - makeAppController: builds the app-track controller, handing it the
    ///     sink its delegate must call. A factory (not an instance) so the
    ///     controller can capture the service's sink without a retain cycle.
    init(pluginChecker: any PluginUpdateChecking,
         engineStates: AnyPublisher<EngineState, Never>,
         makeAppController: (@escaping @MainActor (AppUpdateEvent) -> Void) -> any AppUpdateController) {
        self.pluginChecker = pluginChecker
        self.appUpdate = UpdateStatus()
        let controller = makeAppController { [weak self] event in
            self?.applyAppEvent(event)
        }
        self.appController = controller
        self.appUpdate.currentVersion = controller.currentVersion

        // Every emission already arrives on the main actor in production
        // (EngineHealthService.state is set from @MainActor code, and
        // Combine delivers synchronously within that same call) and in
        // tests (which drive the subject from a @MainActor test function),
        // so the sink can run synchronously via `assumeIsolated` rather than
        // hopping through `.receive(on:)` — which would make delivery
        // asynchronous and tests non-deterministic without a sleep.
        engineStates
            .sink { [weak self] state in
                guard let self else { return }
                MainActor.assumeIsolated {
                    self.handleEngineState(state)
                }
            }
            .store(in: &cancellables)
    }

    var appUpdatesEnabled: Bool { appController?.isEnabled ?? false }
    var anyUpdateAvailable: Bool { availableCount > 0 }
    var availableCount: Int {
        (appUpdate.isAvailable ? 1 : 0) + (pluginRowVisible && pluginUpdate.isAvailable ? 1 : 0)
    }

    // MARK: App track

    func applyAppEvent(_ event: AppUpdateEvent) {
        switch event {
        case .found(let version):
            appUpdate.latestVersion = version
            appUpdate.state = .available
        case .upToDate:
            appUpdate.latestVersion = appUpdate.currentVersion
            appUpdate.state = .upToDate
        case .cycleEnded:
            // Only resolve a check that's still in flight from this
            // service's point of view; a cycle end arriving after `.found`
            // or `.upToDate` already settled the state (e.g. "remind me
            // later" on a found update) must not clobber it.
            guard appUpdate.state == .checking else { return }
            appUpdate.state = appStateBeforeChecking ?? .idle
            appStateBeforeChecking = nil
        case .failed(let message):
            appUpdate.state = .error(message)
        }
    }

    /// Sparkle shows its own UI from here on (found / up to date / error);
    /// we just note that a check is in flight — and only if one actually
    /// started, since Sparkle silently declines (no callback at all) when a
    /// session is already running or a permission prompt is showing.
    func checkApp() {
        guard let controller = appController, controller.isEnabled else { return }
        let priorState = appUpdate.state
        guard controller.checkForUpdates() else { return }
        appStateBeforeChecking = priorState
        appUpdate.state = .checking
    }

    // MARK: Plugin track

    /// Uses the latest engine state received from `engineStates`. A no-op
    /// before the first one has arrived — there is nothing to check against.
    ///
    /// Re-entrant: if a newer call starts (because the engine state changed
    /// again, or a manual "Check now" overlaps the launch check) before this
    /// one's `await` returns, this call's result is discarded instead of
    /// overwriting the newer one — see `pluginCheckGeneration`.
    func checkPlugin() async {
        guard let engineState = latestEngineState else { return }
        pluginCheckGeneration += 1
        let myGeneration = pluginCheckGeneration
        pluginUpdate.state = .checking
        let result = await pluginChecker.check(engine: engineState)
        guard myGeneration == pluginCheckGeneration, !Task.isCancelled else { return }
        applyPluginResult(result)
    }

    private func applyPluginResult(_ result: PluginUpdateResult) {
        guard result.applicable else {
            pluginRowVisible = false
            pluginUpdate = UpdateStatus()
            pluginReleasesURL = nil
            return
        }
        pluginRowVisible = true
        pluginUpdate.currentVersion = result.installed
        pluginUpdate.latestVersion = result.latest
        pluginReleasesURL = result.releasesURL
        if let error = result.error {
            pluginUpdate.state = .error(error)
        } else if result.installed == nil {
            pluginUpdate.state = .idle          // applicable, but nothing installed → row shown, nothing to report
        } else if result.isUpdateAvailable {
            pluginUpdate.state = .available
        } else {
            pluginUpdate.state = .upToDate
        }
    }

    private func handleEngineState(_ state: EngineState) {
        latestEngineState = state
        let key = Self.eligibilityKey(for: state)
        if hasEligibilityKey, key == lastEligibilityKey { return }
        hasEligibilityKey = true
        lastEligibilityKey = key
        check(.plugin)
    }

    /// nil for states the plugin row doesn't apply to (`.managed`,
    /// `.notInstalled`, `.broken`); the external install's root path
    /// otherwise, so a different external engine is treated as a new thing
    /// to check.
    private static func eligibilityKey(for state: EngineState) -> String? {
        switch state {
        case .external(let install, _):
            return install.root.path
        case .managed, .notInstalled, .broken:
            return nil
        }
    }

    // MARK: Triggers

    func check(_ track: UpdateTrack) {
        switch track {
        case .app:
            checkApp()
        case .plugin:
            pluginTask?.cancel()
            pluginTask = Task { [weak self] in await self?.checkPlugin() }
        }
    }

    func checkAll() {
        UpdateTrack.allCases.forEach(check)
    }
}
