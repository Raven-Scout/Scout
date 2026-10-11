import Testing
import Foundation
import Combine
@testable import Scout

/// `Configuration.production()` reads the real home by design — never called
/// from a test. These exercise `testHost()` and `testing(...)` only, which
/// must point `engineLayout` at a temp directory so a test run can never
/// locate (or doctor-check) the user's real engine.
@MainActor
@Suite("AppState engine wiring")
struct AppStateEngineWiringTests {
    @Test func testHostEngineLayoutIsUnderTempNotRealHome() {
        let layout = AppState.Configuration.testHost().engineLayout
        let realHome = FileManager.default.homeDirectoryForCurrentUser
        #expect(layout.home != realHome)
        #expect(layout.home.path.hasPrefix(FileManager.default.temporaryDirectory.path))
    }

    @Test func testingEngineLayoutIsUnderTempNotRealHome() {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("AppStateEngineWiringTests-\(UUID().uuidString)", isDirectory: true)
        let layout = AppState.Configuration.testing(scoutDirectory: tmp).engineLayout
        let realHome = FileManager.default.homeDirectoryForCurrentUser
        #expect(layout.home != realHome)
        #expect(layout.home.path.hasPrefix(tmp.path))
    }

    // `.testing(...)` defaults to `startsBackgroundWork: false`, so there is no
    // launch `Task` to race against — but this used to be a synchronous
    // `@MainActor` test, which meant the assertion below ran before the
    // (nonexistent, in this case) `Task` could ever have started: it could
    // never fail even if `startsBackgroundWork` silently flipped to `true`.
    // Making it `async` and yielding first means a regression would actually
    // be caught — proven by the negative control below, which flips
    // `startsBackgroundWork` on and shows the same shape of test *does*
    // observe a call within this yield window.
    @Test func constructionExposesInitialStateAndRunsNoDoctorCall() async throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("AppStateEngineWiringTests-\(UUID().uuidString)", isDirectory: true)
        let rule = RuleBasedRunner()
        let configuration = AppState.Configuration.testing(scoutDirectory: tmp, runner: rule)
        let appState = AppState(configuration: configuration)
        #expect(appState.engineHealth.state == configuration.initialEngineState)
        try await Task.sleep(for: .milliseconds(100))
        #expect(rule.calls.isEmpty)
    }

    /// Negative control for the test above: with `startsBackgroundWork` forced
    /// on and a real `.managed` engine state backed by an on-disk pointer +
    /// executable `scoutctl`, the launch `Task` must reach `engineHealth`'s
    /// doctor call within the same yield window the positive test uses — this
    /// is what proves that window is long enough to catch a regression, not
    /// just a coincidence of the positive test never starting a `Task` at all.
    ///
    /// Everything lives under a fresh per-test temp directory: `engineLayout`
    /// points `home` at `<tmp>/fake-home` (never the real
    /// `~/.local/state/scout`), and the only "subprocess" is `RuleBasedRunner`
    /// answering in-memory — nothing here shells out for real. `sched.start()`
    /// / `power.start()` also fire (part of the same launch `Task`) and their
    /// polling `Timer`s are not explicitly invalidated when this test's
    /// `AppState` goes out of scope, but both close over `[weak self]`
    /// (`ScheduleService`/`PowerStateService`), so once nothing retains this
    /// test's object graph the timers fire into a nil `self` and become
    /// no-ops — they do not touch other tests' `RuleBasedRunner`s or state.
    @Test func negativeControlDoctorRunsWhenBackgroundWorkIsOn() async throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("AppStateEngineWiringTests-\(UUID().uuidString)", isDirectory: true)
        let fakeHome = tmp.appendingPathComponent("fake-home", isDirectory: true)
        let layout = EngineLayout(home: fakeHome)
        let version = "0.10.0"
        let scoutctlURL = layout.scoutctl(version: version)

        try FileManager.default.createDirectory(at: scoutctlURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: layout.stateDir, withIntermediateDirectories: true)
        try "#!/bin/sh\nexit 0\n".write(to: scoutctlURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scoutctlURL.path)

        let pointer = EnginePointer(
            schemaVersion: 1, version: version,
            engineRoot: layout.engineRoot(version: version).path,
            python: layout.venv(version: version).appendingPathComponent("bin/python").path,
            scoutctl: scoutctlURL.path,
            vault: tmp.path,
            managedBy: "scout-app",
            writtenAt: ""
        )
        try JSONEncoder().encode(pointer).write(to: layout.pointerURL)

        let locator = EngineLocator(layout: layout)
        let initialState = locator.locate()
        guard case .managed = initialState else {
            Issue.record("fixture did not produce a managed engine state: \(initialState)")
            return
        }

        let rule = RuleBasedRunner()
        rule.on(tool: "scoutctl", prefix: ["bootstrap", "doctor", "--json"],
                stdout: #"{"severity":"green","errors":[],"warnings":[]}"#)

        var configuration = AppState.Configuration.testing(scoutDirectory: tmp, runner: rule)
        configuration.startsBackgroundWork = true
        configuration.engineLayout = layout
        configuration.initialEngineState = initialState

        let appState = AppState(configuration: configuration)
        _ = appState // keep the graph alive for the duration of the poll below

        // Poll instead of a single fixed sleep: under real concurrent test
        // load (e.g. the rest of the target's suites also running) a fixed
        // 100ms window is flaky — measured failing when run alongside the
        // view-smoke suites. This still resolves in ~1 poll tick in the
        // common case and only pays the full budget when the host is slow.
        var sawDoctorCall = false
        for _ in 0..<40 {
            if rule.calls(to: "scoutctl").contains(["bootstrap", "doctor", "--json"]) {
                sawDoctorCall = true
                break
            }
            try await Task.sleep(for: .milliseconds(50))
        }
        #expect(sawDoctorCall)
    }

    /// The 10-minute engine re-check must start once the first engine refresh
    /// is done — not wait on the Action Items environment check, whose
    /// `scoutctl action-items --help` probe is wedged here until the test ends.
    /// `.testing(...)`'s engine layout is an empty temp home, so the first
    /// refresh is a fast `.notInstalled` with no doctor call.
    @Test func periodicEngineRefreshStartsWhileTheActionItemsCheckIsWedged() async throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("AppStateEngineWiringTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let wedged = WedgedActionItemsRunner()
        defer { wedged.release() }
        var configuration = AppState.Configuration.testing(scoutDirectory: tmp, runner: wedged)
        configuration.startsBackgroundWork = true
        let appState = AppState(configuration: configuration)

        var scheduled = false
        for _ in 0..<40 {
            if appState.engineHealth.isPeriodicRefreshScheduled { scheduled = true; break }
            try await Task.sleep(for: .milliseconds(50))
        }
        #expect(scheduled)
        #expect(wedged.probeIsInFlight)
        #expect(appState.engineHealth.state == .notInstalled)
    }

    /// `engineHealth` is a nested `ObservableObject` (spec §5, Ruling 32 item
    /// 4): a change it publishes must also fire `AppState.objectWillChange`
    /// — the same forwarding `wishlistDoc`/`researchDoc` already get — so the
    /// window gate and the Settings sidebar badge update without every
    /// observer needing its own subscription to `engineHealth` directly.
    /// Built from `.testing(...)`, never `.production()`/`.live`.
    @Test func engineHealthChangesForwardToAppStateObjectWillChange() async throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("AppStateEngineWiringTests-\(UUID().uuidString)", isDirectory: true)
        let appState = AppState(configuration: .testing(scoutDirectory: tmp))

        var fired = false
        let cancellable = appState.objectWillChange.sink { _ in fired = true }
        defer { cancellable.cancel() }

        appState.engineHealth.objectWillChange.send()
        // The forwarding sink hops through `.receive(on: DispatchQueue.main)`
        // — give the main run loop a tick to deliver it.
        try await Task.sleep(for: .milliseconds(100))

        #expect(fired)
    }
}

// MARK: - C8: onboarding gate, launch-time upgrade, Settings actions

private struct NoDownloads: FileDownloader {
    func download(_ url: URL) async throws -> URL { throw URLError(.notConnectedToInternet) }
}

private func release(_ v: String) -> EngineRelease {
    EngineRelease(schemaVersion: 2, version: v, engine: .init(version: v), uv: .init(version: "0", sha256: [:]))
}

private func tempDirectory(_ label: String) -> URL {
    FileManager.default.temporaryDirectory.appendingPathComponent("AppStateC8-\(label)-\(UUID().uuidString)", isDirectory: true)
}

/// Pure decisions and the test configurations' safety (Ruling 46).
@MainActor
@Suite("AppState engine wiring — C8 decisions")
struct AppStateEngineDecisionTests {
    let install = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil)

    @Test func autoUpgradeOnlyWhenManagedAndBehind() {
        #expect(AppState.shouldAutoUpgrade(state: .managed(install, vaultBootstrapped: true), release: release("0.11.0")))
        #expect(!AppState.shouldAutoUpgrade(state: .managed(install, vaultBootstrapped: true), release: release("0.10.0")))
        #expect(!AppState.shouldAutoUpgrade(state: .managed(install, vaultBootstrapped: false), release: release("0.11.0")))   // finish onboarding first
        #expect(!AppState.shouldAutoUpgrade(state: .external(install, .devCheckout), release: release("0.11.0")))
        #expect(!AppState.shouldAutoUpgrade(state: .managed(install, vaultBootstrapped: true), release: nil))
        #expect(!AppState.shouldAutoUpgrade(state: .notInstalled, release: release("0.11.0")))
        #expect(!AppState.shouldAutoUpgrade(state: .broken(install, reason: "r"), release: release("0.11.0")))
    }

    /// Ruling 46: no test configuration carries a release or finds `claude`,
    /// so nothing built from one can install or upgrade an engine.
    @Test func testConfigurationsCanNeverInstall() async {
        #expect(AppState.Configuration.testHost().engineRelease == nil)
        #expect(AppState.Configuration.testHost().resolveClaude("") == nil)
        let tmp = tempDirectory("no-install")
        defer { try? FileManager.default.removeItem(at: tmp) }
        let configuration = AppState.Configuration.testing(scoutDirectory: tmp)
        #expect(configuration.engineRelease == nil && configuration.engineTarballURL == nil)
        #expect(configuration.resolveClaude("") == nil)
        let appState = AppState(configuration: configuration)
        #expect(await appState.makeInstaller(progress: { _ in }) == nil)
    }

    /// With a release, `makeInstaller` still needs `claude` — resolved from
    /// the user's override, off the main actor.
    @Test func makeInstallerNeedsClaudeAndPassesTheOverride() async {
        let tmp = tempDirectory("make-installer")
        defer { try? FileManager.default.removeItem(at: tmp) }
        let defaults = UserDefaults(suiteName: "scout.tests.\(UUID().uuidString)")!
        defaults.set("/Users/alex/bin/claude", forKey: "claudeCLIPath")
        let seen = OverrideRecorder()
        var configuration = AppState.Configuration.testing(scoutDirectory: tmp, defaults: defaults)
        configuration.engineRelease = release("0.11.0")
        configuration.resolveClaude = { override in seen.record(override, onMain: Thread.isMainThread); return nil }
        #expect(await AppState(configuration: configuration).makeInstaller(progress: { _ in }) == nil)
        configuration.resolveClaude = { override in seen.record(override, onMain: Thread.isMainThread); return override }
        #expect(await AppState(configuration: configuration).makeInstaller(progress: { _ in }) != nil)
        #expect(seen.overrides == ["/Users/alex/bin/claude", "/Users/alex/bin/claude"])
        #expect(!seen.anyOnMain)
    }

    /// Ruling 41: the window's onboarding exists exactly for the states that
    /// gate the tabs, and it knows the vault this process was wired to
    /// (Ruling 46's restart note).
    @Test func onboardingExistsExactlyForGatingStates() {
        let states: [EngineState] = [.notInstalled, .managed(install, vaultBootstrapped: true), .managed(install, vaultBootstrapped: false),
                                     .external(install, .devCheckout), .broken(install, reason: "r"), .broken(nil, reason: "r")]
        for state in states {
            let tmp = tempDirectory("gate")
            defer { try? FileManager.default.removeItem(at: tmp) }
            var configuration = AppState.Configuration.testing(scoutDirectory: tmp)
            configuration.initialEngineState = state
            let appState = AppState(configuration: configuration)
            #expect((appState.onboarding != nil) == state.gatesTabs, "\(state)")
            if let flow = appState.onboarding {
                #expect(flow.appVault == tmp)
                #expect(flow.engineState == state)
                #expect(appState.beginOnboarding() === flow)   // Settings reuses the window's flow
            }
        }
    }

    /// A temp vault whose engine home holds nothing yet (→ `.notInstalled`),
    /// plus a way to "finish setup" on disk: a managed pointer naming an
    /// executable scoutctl, so the next refresh locates `.managed(_, true)`.
    struct GateFixture {
        let tmp: URL
        let layout: EngineLayout
        let runner: RuleBasedRunner
        let appState: AppState

        func writeManagedPointer() throws {
            let scoutctl = layout.scoutctl(version: "0.10.0")
            try FileManager.default.createDirectory(at: scoutctl.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.createDirectory(at: layout.stateDir, withIntermediateDirectories: true)
            try "#!/bin/sh\nexit 0\n".write(to: scoutctl, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scoutctl.path)
            let pointer = EnginePointer(schemaVersion: 1, version: "0.10.0", engineRoot: layout.engineRoot(version: "0.10.0").path,
                                        python: layout.venv(version: "0.10.0").appendingPathComponent("bin/python").path, scoutctl: scoutctl.path, vault: tmp.path, managedBy: "scout-app", writtenAt: "")
            try JSONEncoder().encode(pointer).write(to: layout.pointerURL)
        }
    }

    func gateFixture() -> GateFixture {
        let tmp = tempDirectory("gate-sync")
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor", "--json"], stdout: #"{"severity":"green","errors":[],"warnings":[]}"#)
        var configuration = AppState.Configuration.testing(scoutDirectory: tmp, runner: runner)
        configuration.initialEngineState = .notInstalled
        return GateFixture(tmp: tmp, layout: configuration.engineLayout, runner: runner, appState: AppState(configuration: configuration))
    }

    /// Liveness budget ≥ 10 s (Ruling 69 M7): main-actor suites starve CI.
    func waitUntil(_ condition: () -> Bool) async throws -> Bool {
        for _ in 0..<400 {
            if condition() { return true }
            try await Task.sleep(for: .milliseconds(25))
        }
        return condition()
    }

    /// The gate lifts by itself once the engine stops gating — when the
    /// flow hasn't changed anything yet.
    @Test func onboardingGoesAwayWhenTheEngineStopsGating() async throws {
        let f = gateFixture()
        defer { try? FileManager.default.removeItem(at: f.tmp) }
        #expect(f.appState.onboarding != nil)
        try f.writeManagedPointer()
        await f.appState.engineHealth.refresh()
        #expect(f.appState.engineHealth.state.isManaged)
        #expect(try await waitUntil { f.appState.onboarding == nil })
    }

    /// …but a flow showing Ready stays until the user taps Open Scout,
    /// which re-checks the engine and then drops it.
    @Test func aFinishingFlowStaysUntilOpenScout() async throws {
        let f = gateFixture()
        defer { try? FileManager.default.removeItem(at: f.tmp) }
        guard let flow = f.appState.onboarding else { Issue.record("expected a flow"); return }
        flow.step = .ready
        try f.writeManagedPointer()
        await f.appState.engineHealth.refresh()
        try await Task.sleep(for: .milliseconds(100))
        #expect(f.appState.onboarding === flow)
        flow.finish()
        #expect(try await waitUntil { f.appState.onboarding == nil })
    }

    /// Open Scout while the engine still gates (setup didn't take) starts
    /// a fresh flow rather than leaving the window blank.
    @Test func finishingWhileStillGatedStartsAFreshFlow() async throws {
        let f = gateFixture()
        defer { try? FileManager.default.removeItem(at: f.tmp) }
        guard let flow = f.appState.onboarding else { Issue.record("expected a flow"); return }
        flow.finish()
        #expect(try await waitUntil { f.appState.onboarding.map { $0 !== flow } ?? false })
    }

    /// Ruling 69 M4: Settings' "Set up…" gets no flow when the engine
    /// doesn't gate (there is nothing to set up), and the window's own flow
    /// when it does.
    @Test func beginOnboardingOnlyWhenGating() {
        let tmp = tempDirectory("begin")
        defer { try? FileManager.default.removeItem(at: tmp) }
        let appState = AppState(configuration: .testing(scoutDirectory: tmp))   // external: usable
        #expect(appState.beginOnboarding() == nil && appState.onboarding == nil)
        let f = gateFixture()
        defer { try? FileManager.default.removeItem(at: f.tmp) }
        let flow = f.appState.onboarding
        #expect(flow != nil && f.appState.beginOnboarding() === flow)
    }

    /// Ruling 69 M4: a held flow that stops holding the window (Back from a
    /// finished vault step) goes away without any engine-state change.
    @Test func aFlowThatStopsHoldingIsDroppedWithoutAStateChange() async throws {
        let f = gateFixture()
        defer { try? FileManager.default.removeItem(at: f.tmp) }
        guard let flow = f.appState.onboarding else { Issue.record("expected a flow"); return }
        flow.step = .ready
        try f.writeManagedPointer()
        await f.appState.engineHealth.refresh()       // no longer gating; Ready holds the flow
        try await Task.sleep(for: .milliseconds(100))
        #expect(f.appState.onboarding === flow)
        flow.step = .connectors                        // not holding any more
        #expect(try await waitUntil { f.appState.onboarding == nil })
    }

    /// Ruling 69 I3: services reach an engine the app manages (or will
    /// install) through the shim, which bootstrap re-renders on every
    /// install/upgrade; an external engine keeps its own scoutctl.
    @Test func servicesUseTheShimForEveryEngineTheAppOwns() {
        let layout = EngineLayout(home: tempDirectory("shim"))
        let i = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/Users/alex/scout-plugin/.venv/bin/scoutctl"),
                              python: nil, version: "0.10.0", vault: nil)
        for state in [EngineState.notInstalled, .managed(i, vaultBootstrapped: true), .managed(i, vaultBootstrapped: false),
                      .broken(i, reason: "r"), .broken(nil, reason: "r")] {
            #expect(AppState.serviceScoutctl(for: state, layout: layout) == layout.shimURL, "\(state)")
        }
        #expect(AppState.serviceScoutctl(for: .external(i, .devCheckout), layout: layout) == i.scoutctl)
    }

    /// Ruling 69 I3: the Action Items check runs again once the engine
    /// stops gating (onboarding finished), so the banner follows.
    @Test func actionItemsCheckRerunsWhenTheEngineStopsGating() async throws {
        let f = gateFixture()
        defer { try? FileManager.default.removeItem(at: f.tmp) }
        f.runner.on(tool: "false", prefix: ["action-items", "--help"], stdout: "Usage: scoutctl action-items")
        f.appState.actionItemsEnvState.result = ActionItemsEnvironmentResult(ok: false, message: "Scout engine not found — see Settings ▸ Engine.")
        try f.writeManagedPointer()
        await f.appState.engineHealth.refresh()
        #expect(try await waitUntil { f.appState.actionItemsEnvState.result.ok })
        #expect(f.runner.calls.contains { $0.arguments == ["action-items", "--help"] })
    }

    /// With background work off, a managed engine behind the bundled one is
    /// NOT upgraded by constructing the app state (Ruling 46).
    @Test func constructionNeverUpgrades() async throws {
        let tmp = tempDirectory("no-launch-upgrade")
        defer { try? FileManager.default.removeItem(at: tmp) }
        let runner = RuleBasedRunner()
        var configuration = AppState.Configuration.testing(scoutDirectory: tmp, runner: runner)
        configuration.engineRelease = release("0.11.0")
        configuration.resolveClaude = { _ in "/Users/alex/.local/bin/claude" }
        configuration.initialEngineState = .managed(install, vaultBootstrapped: true)
        let appState = AppState(configuration: configuration)
        try await Task.sleep(for: .milliseconds(100))
        #expect(runner.calls.isEmpty)
        #expect(appState.engineUpgradeProgress == nil && !appState.isUpgradingEngine)
    }

    /// No `claude`: the sheet shows why, with no installer run, and Later
    /// hides it.
    @Test func upgradeWithoutClaudeExplainsAndCanBeDismissed() async {
        let tmp = tempDirectory("no-claude")
        defer { try? FileManager.default.removeItem(at: tmp) }
        let runner = RuleBasedRunner()
        var configuration = AppState.Configuration.testing(scoutDirectory: tmp, runner: runner)
        configuration.engineRelease = release("0.11.0")
        configuration.initialEngineState = .managed(install, vaultBootstrapped: true)
        let appState = AppState(configuration: configuration)
        await appState.runEngineUpgradeIfNeeded()
        #expect(appState.engineUpgradeProgress == [:])
        #expect(appState.engineUpgradeError?.contains("Claude Code wasn't found") == true)
        #expect(runner.calls.isEmpty)
        appState.dismissEngineUpgrade()
        #expect(appState.engineUpgradeProgress == nil && appState.engineUpgradeError == nil)
    }
}

private final class OverrideRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [String] = []
    private var onMain = false
    func record(_ override: String, onMain main: Bool) { lock.withLock { items.append(override); onMain = onMain || main } }
    var overrides: [String] { lock.withLock { items } }
    var anyOnMain: Bool { lock.withLock { onMain } }
}

/// The upgrade path end to end against a temp home: a real 0.10.0 install
/// (real tar, a fake install-venv.sh, a simulated Claude Code, scripted
/// `scoutctl` whose `bootstrap auto` rewrites the pointer like the engine
/// does), then the bundled 0.11.0 applied through `AppState`. Never `.live`.
@MainActor
@Suite("AppState engine upgrade", .serialized)
struct AppStateEngineUpgradeTests {
    struct Fixture {
        let layout: EngineLayout
        let runner: RuleBasedRunner
        let claudeCode: FakeClaudeCode?
        let release: EngineRelease
        let tarball: URL
        let vault: URL
        let claude: String
        /// One throwaway suite shared by every AppState built over this
        /// fixture, so a "relaunch" sees what the last launch remembered
        /// (final review I2). Never `.standard`.
        let defaultsSuite: String
        var defaults: UserDefaults { UserDefaults(suiteName: defaultsSuite)! }
        var home: URL { layout.home }

        func remove() {
            try? FileManager.default.removeItem(at: home)
            UserDefaults().removePersistentDomain(forName: defaultsSuite)
        }
    }

    /// Answers `bootstrap doctor --json` red for the NEW venv's scoutctl
    /// when set — a verify failure after the switch — and makes the NEW
    /// venv's `bootstrap auto` refuse when `newVersionRefuses` is set.
    final class DoctorSwitch: @unchecked Sendable {
        private let lock = NSLock()
        private var red = false
        private var refuses = false
        var newVersionIsRed: Bool {
            get { lock.withLock { red } }
            set { lock.withLock { red = newValue } }
        }
        var newVersionRefuses: Bool {
            get { lock.withLock { refuses } }
            set { lock.withLock { refuses = newValue } }
        }
    }

    static func pointer(_ layout: EngineLayout, version: String, vault: URL) throws {
        try FileManager.default.createDirectory(at: layout.stateDir, withIntermediateDirectories: true)
        let pointer = EnginePointer(
            schemaVersion: 1, version: version, engineRoot: layout.engineRoot(version: version).path,
            python: layout.venv(version: version).appendingPathComponent("bin/python").path,
            scoutctl: layout.scoutctl(version: version).path, vault: vault.path, managedBy: "scout-app", writtenAt: "")
        try JSONEncoder().encode(pointer).write(to: layout.pointerURL)
    }

    func fixture(venvBuildFails: Bool = false, doctor: DoctorSwitch = DoctorSwitch()) async throws -> Fixture {
        let support = EngineInstallerTests()
        let (f10, f11) = try await support.installedThenUpgradeFixture(venvBuildFails: venvBuildFails)
        let layout = f10.layout
        let vault = layout.home.appendingPathComponent("Scout")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
        try Self.pointer(layout, version: "0.10.0", vault: vault)
        // Two older versions: a successful upgrade's GC keeps 0.11.0 and one
        // previous (0.10.0) and removes both; GC run after a failure would
        // keep 0.10.0 (still `current`) and 0.9.0 (one previous) but remove
        // 0.8.0 — which is what proves GC never runs on failure.
        for v in ["0.8.0", "0.9.0"] {
            for dir in [layout.engineRoot(version: v), layout.venv(version: v)] {
                try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            }
        }
        // `bootstrap auto` from venv/<v> rewrites the pointer to <v>, as the
        // engine's `bootstrap upgrade` does — the atomic switch.
        f11.runner.on({ url, args in url.lastPathComponent == "scoutctl" && args.starts(with: ["bootstrap", "auto"]) }) { url, _, _ in
            let version = url.deletingLastPathComponent().deletingLastPathComponent().lastPathComponent
            if doctor.newVersionRefuses && version == "0.11.0" {
                return ProcessResult(exitCode: 1, stdout: Data(#"{"schema_version":1,"action":"refused","reason":"","dry_run":false,"vault":"\#(vault.path)","plugin_version":"0.11.0","error":"vault has unresolved conflicts","doctor":null,"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":null}"#.utf8), stderr: Data())
            }
            try Self.pointer(layout, version: version, vault: vault)
            return ProcessResult(exitCode: 0, stdout: Data(#"{"schema_version":1,"action":"upgrade","reason":"","dry_run":false,"vault":"\#(vault.path)","plugin_version":"\#(version)","error":null,"doctor":{"severity":"green","errors":[],"warnings":[]},"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":"p"}"#.utf8), stderr: Data())
        }
        f11.runner.on({ url, args in url.lastPathComponent == "scoutctl" && args == ["bootstrap", "doctor", "--json"] }) { url, _, _ in
            let version = url.deletingLastPathComponent().deletingLastPathComponent().lastPathComponent
            let red = doctor.newVersionIsRed && version == "0.11.0"
            let json = red ? #"{"severity":"red","errors":["launchd: com.scout.heartbeat not registered"],"warnings":[]}"#
                           : #"{"severity":"green","errors":[],"warnings":[]}"#
            return ProcessResult(exitCode: red ? 2 : 0, stdout: Data(json.utf8), stderr: Data())
        }
        f11.runner.on(tool: "false", prefix: ["action-items", "--help"], stdout: "Usage: scoutctl action-items")
        return Fixture(layout: layout, runner: f11.runner, claudeCode: f11.claudeCode, release: f11.release, tarball: f11.tarball,
                       vault: vault, claude: f10.claude.path, defaultsSuite: "scout.tests.\(UUID().uuidString)")
    }

    /// A fresh AppState over the fixture's home, as a (re)launch would build.
    func appState(_ f: Fixture, startsBackgroundWork: Bool = false) -> AppState {
        let claude = f.claude
        var configuration = AppState.Configuration.testing(scoutDirectory: f.vault, runner: f.runner, defaults: f.defaults)
        configuration.engineLayout = f.layout
        configuration.initialEngineState = EngineLocator(layout: f.layout).locate()
        configuration.engineRelease = f.release
        configuration.engineTarballURL = f.tarball
        configuration.resolveClaude = { _ in claude }
        configuration.fileDownloader = NoDownloads()
        configuration.startsBackgroundWork = startsBackgroundWork
        return AppState(configuration: configuration)
    }

    func current(_ layout: EngineLayout) -> String? { try? FileManager.default.destinationOfSymbolicLink(atPath: layout.currentEngineLink.path) }
    func pluginVersion(_ layout: EngineLayout) -> String? { ClaudePluginsRegistry.scoutPlugin(pluginsDir: layout.claudePluginsDir)?.version }

    func waitUntil(_ condition: () -> Bool) async throws -> Bool {
        for _ in 0..<400 {
            if condition() { return true }
            try await Task.sleep(for: .milliseconds(25))
        }
        return condition()
    }

    @Test(.timeLimit(.minutes(1))) func successfulUpgradeRunsTheUpgradeStepsThenCollectsGarbage() async throws {
        let f = try await fixture()
        defer { f.remove() }
        let s = appState(f)
        #expect(s.engineHealth.state.install?.version == "0.10.0")
        let callsBefore = f.runner.calls.count
        await s.runEngineUpgradeIfNeeded()

        #expect(s.engineUpgradeProgress == nil)        // sheet closed
        #expect(s.engineUpgradeError == nil && !s.isUpgradingEngine && !s.engineUpgradeIsRepair)
        #expect(current(f.layout) == f.layout.engineRoot(version: "0.11.0").path)
        #expect(pluginVersion(f.layout) == "0.11.0")
        #expect(s.engineHealth.state.install?.version == "0.11.0" && !s.engineSwitchUnfinished)
        let calls = f.runner.calls.dropFirst(callsBefore).map(\.arguments)
        let bootstrap = calls.firstIndex { $0.starts(with: ["bootstrap", "auto"]) }
        let marketplace = calls.firstIndex(of: ClaudeCodeCLI.marketplaceUpdate)
        let plugin = calls.firstIndex(of: ClaudeCodeCLI.pluginUpdate)
        #expect(bootstrap != nil && marketplace != nil && plugin != nil)
        if let bootstrap, let marketplace, let plugin { #expect(bootstrap < marketplace && marketplace < plugin) }
        // Ruling 69 I3: the Action Items check re-ran after the upgrade.
        #expect(calls.contains(["action-items", "--help"]))
        // GC after success: the current version and one previous stay.
        for v in ["0.8.0", "0.9.0"] {
            #expect(!FileManager.default.fileExists(atPath: f.layout.engineRoot(version: v).path))
            #expect(!FileManager.default.fileExists(atPath: f.layout.venv(version: v).path))
        }
        #expect(FileManager.default.fileExists(atPath: f.layout.engineRoot(version: "0.10.0").path))
    }

    @Test(.timeLimit(.minutes(1))) func failedUpgradeKeepsTheSheetTheOldEngineAndEveryVersion() async throws {
        let f = try await fixture(venvBuildFails: true)
        defer { f.remove() }
        let s = appState(f)
        await s.runEngineUpgradeIfNeeded()

        guard case .failed? = s.engineUpgradeProgress?[.buildVenv]?.status else {
            Issue.record("expected the sheet to show buildVenv failed: \(String(describing: s.engineUpgradeProgress))"); return
        }
        #expect(EngineUpgradeSheet.failure(progress: s.engineUpgradeProgress ?? [:], error: s.engineUpgradeError)?.contains("install-venv.sh failed") == true)
        #expect(EngineUpgradeSheet.statusNote(s.engineUpgradeProgress ?? [:]).contains("current engine keeps working"))
        #expect(!s.isUpgradingEngine)
        #expect(current(f.layout) == f.layout.engineRoot(version: "0.10.0").path)
        for v in ["0.8.0", "0.9.0"] {   // no GC on failure
            #expect(FileManager.default.fileExists(atPath: f.layout.engineRoot(version: v).path))
        }
        #expect(s.engineHealth.state.isManaged)
        s.dismissEngineUpgrade()
        #expect(s.engineUpgradeProgress == nil)
    }

    /// Ruling 46: the automatic upgrade runs from the launch task — and only
    /// there, so this one turns background work on.
    @Test(.timeLimit(.minutes(1))) func launchUpgradesAManagedEngineBehindTheBundle() async throws {
        let f = try await fixture()
        defer { f.remove() }
        let s = appState(f, startsBackgroundWork: true)
        #expect(try await waitUntil { pluginVersion(f.layout) == "0.11.0" && !s.isUpgradingEngine })
        #expect(current(f.layout) == f.layout.engineRoot(version: "0.11.0").path)
        #expect(f.runner.calls(to: "claude").contains(ClaudeCodeCLI.marketplaceUpdate))
    }

    /// Ruling 69 I1/M6: `marketplace update` fails AFTER the switch —
    /// `bootstrap upgrade` rewrote the pointer to 0.11.0 and `current` already
    /// moved, but Claude Code still loads 0.10.0. That state must stay
    /// actionable (Finish update). Final review I2: the launch remembered the
    /// failure, so the next launch does NOT re-open the sheet for the same
    /// target; Settings ▸ Engine's Finish update finishes it and clears the
    /// memo.
    @Test(.timeLimit(.minutes(1))) func aFailureAfterTheSwitchBacksOffThenFinishUpdateFinishesIt() async throws {
        let f = try await fixture()
        defer { f.remove() }
        f.claudeCode?.failNext(ClaudeCodeCLI.marketplaceUpdate)
        let first = appState(f)
        await first.runEngineUpgradeIfNeeded()

        guard case .failed? = first.engineUpgradeProgress?[.registerWithClaudeCode]?.status else {
            Issue.record("expected register to fail: \(String(describing: first.engineUpgradeProgress))"); return
        }
        #expect(EngineUpgradeSheet.statusNote(first.engineUpgradeProgress ?? [:]).contains("finishes switching Claude Code"))
        #expect(current(f.layout) == f.layout.engineRoot(version: "0.11.0").path)   // `current` already moved
        #expect(pluginVersion(f.layout) == "0.10.0")
        #expect(first.engineHealth.state.install?.version == "0.11.0")              // the pointer switched
        #expect(first.engineSwitchUnfinished)
        #expect(EngineUpgradeFailureMemo.load(from: f.defaults) == .init(targetVersion: "0.11.0", failedStep: .registerWithClaudeCode))
        first.dismissEngineUpgrade()                                                // Later

        // The next launch backs off: no sheet, no installer run.
        let relaunched = appState(f)
        let callsBefore = f.runner.calls.count
        #expect(AppState.shouldAutoUpgrade(state: relaunched.engineHealth.state, release: f.release, switchUnfinished: relaunched.engineSwitchUnfinished))
        await relaunched.runEngineUpgradeIfNeeded()
        #expect(relaunched.engineUpgradeProgress == nil && !relaunched.isUpgradingEngine)
        #expect(f.runner.calls.count == callsBefore)
        // …and Settings ▸ Engine still carries it.
        let settings = EngineSettingsModel(state: relaunched.engineHealth.state, doctor: relaunched.engineHealth.doctor, lastError: nil,
                                           bundledVersion: f.release.engine.version, unfinishedSwitch: relaunched.engineSwitchUnfinished)
        #expect(settings.upgradeAction == .finishUpdate && settings.canUpdate)

        await relaunched.runEngineUpgrade()                                         // Finish update
        #expect(pluginVersion(f.layout) == "0.11.0" && !relaunched.engineSwitchUnfinished)
        #expect(relaunched.engineUpgradeProgress == nil && relaunched.engineUpgradeIsRepair)   // target == installed
        #expect(EngineUpgradeFailureMemo.load(from: f.defaults) == nil)
    }

    /// Final review I2: a launch upgrade that fails at `bootstrapVault` (the
    /// vault refused; nothing switched) is remembered, so the next launch
    /// leaves it to Settings ▸ Engine's Update — which clears the memo and,
    /// once the cause is fixed, succeeds.
    @Test(.timeLimit(.minutes(1))) func aBootstrapFailureBacksOffUntilSettingsUpdates() async throws {
        let doctor = DoctorSwitch()
        doctor.newVersionRefuses = true
        let f = try await fixture(doctor: doctor)
        defer { f.remove() }
        let first = appState(f)
        await first.runEngineUpgradeIfNeeded()
        guard case .failed? = first.engineUpgradeProgress?[.bootstrapVault]?.status else {
            Issue.record("expected bootstrapVault to fail: \(String(describing: first.engineUpgradeProgress))"); return
        }
        #expect(current(f.layout) == f.layout.engineRoot(version: "0.10.0").path)   // nothing switched
        #expect(EngineUpgradeFailureMemo.load(from: f.defaults) == .init(targetVersion: "0.11.0", failedStep: .bootstrapVault))

        let relaunched = appState(f)
        let callsBefore = f.runner.calls.count
        await relaunched.runEngineUpgradeIfNeeded()
        #expect(relaunched.engineUpgradeProgress == nil && f.runner.calls.count == callsBefore)
        let settings = EngineSettingsModel(state: relaunched.engineHealth.state, doctor: relaunched.engineHealth.doctor, lastError: nil,
                                           bundledVersion: f.release.engine.version, unfinishedSwitch: relaunched.engineSwitchUnfinished)
        #expect(settings.upgradeAction == .update && settings.canUpdate)

        doctor.newVersionRefuses = false
        await relaunched.runEngineUpgrade()                                         // Settings ▸ Engine ▸ Update
        #expect(relaunched.engineUpgradeProgress == nil && pluginVersion(f.layout) == "0.11.0")
        #expect(EngineUpgradeFailureMemo.load(from: f.defaults) == nil)
    }

    /// Final review I2: failures before `bootstrapVault` leave the old
    /// engine fully live and are often transient, so they aren't remembered
    /// and the next launch tries again.
    @Test(.timeLimit(.minutes(1))) func anEarlyLaunchFailureIsRetriedOnTheNextLaunch() async throws {
        let f = try await fixture(venvBuildFails: true)
        defer { f.remove() }
        await appState(f).runEngineUpgradeIfNeeded()
        #expect(EngineUpgradeFailureMemo.load(from: f.defaults) == nil)
        let relaunched = appState(f)
        await relaunched.runEngineUpgradeIfNeeded()
        guard case .failed? = relaunched.engineUpgradeProgress?[.buildVenv]?.status else {
            Issue.record("expected the relaunch to try again: \(String(describing: relaunched.engineUpgradeProgress))"); return
        }
    }

    /// Final review I2: a manual run clears the memo BEFORE it starts (and,
    /// being manual, records nothing when it fails), so the next launch is
    /// free to try again.
    @Test(.timeLimit(.minutes(1))) func aManualRunClearsTheMemoEvenWhenItFails() async throws {
        let f = try await fixture(venvBuildFails: true)
        defer { f.remove() }
        EngineUpgradeFailureMemo(targetVersion: "0.11.0", failedStep: .verify).save(to: f.defaults)
        let s = appState(f)
        await s.runEngineUpgrade()
        guard case .failed? = s.engineUpgradeProgress?[.buildVenv]?.status else {
            Issue.record("expected the manual run to fail at buildVenv"); return
        }
        #expect(EngineUpgradeFailureMemo.load(from: f.defaults) == nil)
    }

    /// Final review I2: the memo is per target — a newer bundled engine
    /// than the one that failed upgrades automatically, and its success
    /// clears the memo.
    @Test(.timeLimit(.minutes(1))) func aDifferentBundledTargetIgnoresTheMemo() async throws {
        let f = try await fixture()
        defer { f.remove() }
        EngineUpgradeFailureMemo(targetVersion: "0.10.5", failedStep: .registerWithClaudeCode).save(to: f.defaults)
        let s = appState(f)
        await s.runEngineUpgradeIfNeeded()
        #expect(pluginVersion(f.layout) == "0.11.0" && s.engineUpgradeProgress == nil)
        #expect(EngineUpgradeFailureMemo.load(from: f.defaults) == nil)
    }

    /// Ruling 69 M6/M2/I4: only `verify` fails after the switch — Claude Code
    /// already moved, so the switch is finished; the red doctor makes
    /// Settings offer Repair…, and the sheet doesn't claim Retry switches
    /// Claude Code over.
    @Test(.timeLimit(.minutes(1))) func aVerifyFailureAfterTheSwitchOffersRepair() async throws {
        let doctor = DoctorSwitch()
        doctor.newVersionIsRed = true
        let f = try await fixture(doctor: doctor)
        defer { f.remove() }
        let s = appState(f)
        await s.runEngineUpgradeIfNeeded()

        guard case .failed? = s.engineUpgradeProgress?[.verify]?.status else {
            Issue.record("expected verify to fail: \(String(describing: s.engineUpgradeProgress))"); return
        }
        let note = EngineUpgradeSheet.statusNote(s.engineUpgradeProgress ?? [:])
        #expect(!note.contains("switching Claude Code") && note.contains("health check"))
        #expect(pluginVersion(f.layout) == "0.11.0" && !s.engineSwitchUnfinished)
        #expect(s.engineHealth.doctor?.severity == .red)
        let settings = EngineSettingsModel(state: s.engineHealth.state, doctor: s.engineHealth.doctor, lastError: nil,
                                           bundledVersion: f.release.engine.version, unfinishedSwitch: s.engineSwitchUnfinished)
        #expect(settings.upgradeAction == .repair)
        #expect(!AppState.shouldAutoUpgrade(state: s.engineHealth.state, release: f.release, switchUnfinished: s.engineSwitchUnfinished))
        #expect(EngineUpgradeFailureMemo.load(from: f.defaults) == .init(targetVersion: "0.11.0", failedStep: .verify))

        doctor.newVersionIsRed = false                  // Repair… re-runs the idempotent steps
        await s.runEngineUpgrade()
        #expect(s.engineUpgradeProgress == nil && s.engineUpgradeIsRepair)
        #expect(EngineUpgradeFailureMemo.load(from: f.defaults) == nil)
    }

    /// Ruling 69 M5 + no downgrades: the upgrade refuses a vault that isn't
    /// set up and an engine newer than the bundle.
    @Test func upgradeRefusesUnsetVaultsAndDowngrades() async {
        #expect(AppState.wouldDowngrade(installed: "0.12.0", bundled: "0.11.0"))
        #expect(!AppState.wouldDowngrade(installed: "0.11.0", bundled: "0.11.0"))
        #expect(!AppState.wouldDowngrade(installed: nil, bundled: "0.11.0"))
        for state in [EngineState.managed(EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil), vaultBootstrapped: false),
                      .managed(EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.12.0", vault: nil), vaultBootstrapped: true)] {
            let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("AppStateC8-refuse-\(UUID().uuidString)")
            defer { try? FileManager.default.removeItem(at: tmp) }
            let runner = RuleBasedRunner()
            var configuration = AppState.Configuration.testing(scoutDirectory: tmp, runner: runner)
            configuration.engineRelease = EngineRelease(schemaVersion: 2, version: "0.11.0", engine: .init(version: "0.11.0"), uv: .init(version: "0", sha256: [:]))
            configuration.resolveClaude = { _ in "/Users/alex/.local/bin/claude" }
            configuration.initialEngineState = state
            let s = AppState(configuration: configuration)
            await s.runEngineUpgrade()
            #expect(s.engineUpgradeProgress == nil && runner.calls.isEmpty, "\(state)")
        }
    }
}

/// A `ProcessRunner` whose `action-items --help` probe hangs until `release()`
/// — a wedged `scoutctl`. Every other call answers an empty success at once.
/// Nothing shells out.
private final class WedgedActionItemsRunner: ProcessRunner, @unchecked Sendable {
    private let lock = NSLock()
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var released = false
    private var inFlight = 0

    func run(executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?) async throws -> ProcessResult {
        if arguments.starts(with: ["action-items", "--help"]) {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                let resumeNow = lock.withLock { () -> Bool in
                    if released { return true }
                    inFlight += 1
                    waiters.append(continuation)
                    return false
                }
                if resumeNow { continuation.resume() }
            }
        }
        return ProcessResult(exitCode: 0, stdout: Data(), stderr: Data())
    }

    /// True while a probe is parked — i.e. the Action Items check has not finished.
    var probeIsInFlight: Bool { lock.withLock { inFlight > 0 } }

    func release() {
        let pending = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
            released = true
            inFlight = 0
            defer { waiters = [] }
            return waiters
        }
        pending.forEach { $0.resume() }
    }
}
