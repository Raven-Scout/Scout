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
