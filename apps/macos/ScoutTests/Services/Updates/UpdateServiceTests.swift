import Testing
import Foundation
import Combine
@testable import Scout

@MainActor
private final class FakeAppController: AppUpdateController {
    let isEnabled: Bool
    let currentVersion: String? = "0.11.2"
    var checkCalls = 0
    /// What `checkForUpdates()` returns — mirrors Sparkle's
    /// `canCheckForUpdates` gate, which silently declines (no callback at
    /// all) when a session is already in progress or a permission prompt is
    /// showing.
    var startsCheck = true
    var emit: (@MainActor (AppUpdateEvent) -> Void)?
    init(isEnabled: Bool) { self.isEnabled = isEnabled }
    @discardableResult
    func checkForUpdates() -> Bool {
        checkCalls += 1
        return startsCheck
    }
}

/// Records the `EngineState`s it was called with, so tests can assert both
/// the call count (did a recheck happen) and what the service handed it.
private actor FakePluginChecker: PluginUpdateChecking {
    private(set) var calls: [EngineState] = []
    private let results: [PluginUpdateResult]
    private var nextIndex = 0

    init(result: PluginUpdateResult) { self.results = [result] }
    init(results: [PluginUpdateResult]) { self.results = results }

    func check(engine: EngineState) async -> PluginUpdateResult {
        calls.append(engine)
        defer { nextIndex = min(nextIndex + 1, results.count - 1) }
        return results[min(nextIndex, results.count - 1)]
    }
}

/// A checker whose *first* call suspends until the test calls `release()`,
/// so a test can deterministically make an earlier check resolve *after* a
/// later one — no sleeps, no timing races. Every call after the first
/// returns immediately with the next configured result.
private actor GatedPluginChecker: PluginUpdateChecking {
    private(set) var calls: [EngineState] = []
    private let results: [PluginUpdateResult]
    private var callIndex = 0
    private var waiters: [CheckedContinuation<Void, Never>] = []

    init(results: [PluginUpdateResult]) { self.results = results }

    func check(engine: EngineState) async -> PluginUpdateResult {
        let index = callIndex
        callIndex += 1
        calls.append(engine)
        if index == 0 {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                waiters.append(continuation)
            }
        }
        return results[min(index, results.count - 1)]
    }

    /// Resumes the first call, letting its (by now superseded) result resolve.
    func release() {
        waiters.forEach { $0.resume() }
        waiters.removeAll()
    }
}

private let defaultEngineRoot = URL(fileURLWithPath: "/Users/alex/scout-plugin")
private let defaultInstall = EngineInstall(
    root: defaultEngineRoot,
    scoutctl: defaultEngineRoot.appendingPathComponent(".venv/bin/scoutctl"),
    python: nil, version: "1.0.0", vault: nil
)

private func install(named name: String) -> EngineInstall {
    let root = URL(fileURLWithPath: "/Users/alex/\(name)")
    return EngineInstall(root: root, scoutctl: root.appendingPathComponent(".venv/bin/scoutctl"),
                         python: nil, version: "1.0.0", vault: nil)
}

@Suite("UpdateService")
@MainActor
struct UpdateServiceTests {
    /// Builds a service whose `engineStates` replays `engineState` immediately
    /// (mirroring `@Published`'s replay-on-subscribe), like production. Does
    /// NOT await the resulting launch check — callers that care about its
    /// outcome `await service.pluginTask?.value` themselves.
    private func make(
        appEnabled: Bool = true,
        plugin: PluginUpdateResult = PluginUpdateResult(
            applicable: true, installed: "0.7.2", latest: "0.7.2", isUpdateAvailable: false, releasesURL: nil, error: nil
        ),
        engineState: EngineState = .external(defaultInstall, .marketplaceCache)
    ) -> (UpdateService, FakeAppController, FakePluginChecker) {
        let subject = CurrentValueSubject<EngineState, Never>(engineState)
        let checker = FakePluginChecker(result: plugin)
        var controller: FakeAppController!
        let service = UpdateService(pluginChecker: checker, engineStates: subject.eraseToAnyPublisher()) { emit in
            let c = FakeAppController(isEnabled: appEnabled)
            c.emit = emit
            controller = c
            return c
        }
        return (service, controller, checker)
    }

    /// Builds a service against a caller-supplied (and caller-driven) subject,
    /// for tests that need to emit several engine states over time.
    private func makeService(
        appEnabled: Bool = true,
        checker: any PluginUpdateChecking,
        engineStates: AnyPublisher<EngineState, Never>
    ) -> (UpdateService, FakeAppController) {
        var controller: FakeAppController!
        let service = UpdateService(pluginChecker: checker, engineStates: engineStates) { emit in
            let c = FakeAppController(isEnabled: appEnabled)
            c.emit = emit
            controller = c
            return c
        }
        return (service, controller)
    }

    // MARK: App track (unchanged from the old plan)

    @Test func startsIdleWithCurrentVersions() {
        let (service, _, _) = make()
        #expect(service.appUpdate.state == .idle)
        #expect(service.appUpdate.currentVersion == "0.11.2")
        #expect(service.pluginUpdate.state == .idle)
        #expect(service.anyUpdateAvailable == false)
        #expect(service.availableCount == 0)
    }

    @Test func checkAppMarksCheckingAndAsksTheController() {
        let (service, controller, _) = make()
        service.checkApp()
        #expect(service.appUpdate.state == .checking)
        #expect(controller.checkCalls == 1)
    }

    @Test func checkAppIsANoOpWhenUpdaterDisabled() {
        let (service, controller, _) = make(appEnabled: false)
        service.checkApp()
        #expect(service.appUpdatesEnabled == false)
        #expect(service.appUpdate.state == .idle)
        #expect(controller.checkCalls == 0)
    }

    @Test func appEventsDriveTheAppTrack() {
        let (service, controller, _) = make()
        controller.emit?(.found(version: "0.12.0"))
        #expect(service.appUpdate.state == .available)
        #expect(service.appUpdate.latestVersion == "0.12.0")
        #expect(service.anyUpdateAvailable)
        #expect(service.availableCount == 1)

        controller.emit?(.upToDate)
        #expect(service.appUpdate.state == .upToDate)
        #expect(service.anyUpdateAvailable == false)

        controller.emit?(.failed("offline"))
        #expect(service.appUpdate.state == .error("offline"))
    }

    /// Regression for the reviewer's finding: Sparkle silently declines (no
    /// delegate callback at all) when a session is already in progress or a
    /// permission prompt is showing — `checkForUpdates()` reports that via
    /// its return value, and `checkApp()` must not mark `.checking` for a
    /// check that never started.
    @Test func checkAppLeavesStateUnchangedWhenTheControllerDeclinesToStart() {
        let (service, controller, _) = make()
        controller.startsCheck = false
        service.checkApp()
        #expect(service.appUpdate.state == .idle)
        #expect(controller.checkCalls == 1)
    }

    /// Regression for the reviewer's finding: a cancelled check (dismissing
    /// the "checking…" sheet) aborts with a nil error, which never reaches
    /// `didAbortWithError:` — only `didFinishUpdateCycleForUpdateCheck:`
    /// fires, mapped to `.cycleEnded`. Without this, `.checking` was stuck
    /// forever and `CheckForUpdatesView` stayed disabled.
    @Test func cycleEndedWhileCheckingRestoresThePriorState() {
        let (service, controller, _) = make()
        controller.emit?(.upToDate)   // some settled state before the next check
        #expect(service.appUpdate.state == .upToDate)

        service.checkApp()
        #expect(service.appUpdate.state == .checking)
        controller.emit?(.cycleEnded)
        #expect(service.appUpdate.state == .upToDate)   // restored, not stuck
    }

    /// With no prior check in this service's lifetime, there is nothing to
    /// restore to, so `.cycleEnded` falls back to `.idle`.
    @Test func cycleEndedWhileCheckingWithNoPriorStateFallsBackToIdle() {
        let (service, controller, _) = make()
        service.checkApp()
        #expect(service.appUpdate.state == .checking)
        controller.emit?(.cycleEnded)
        #expect(service.appUpdate.state == .idle)
    }

    /// A `.found` (or any other settled result) that arrives before the
    /// cycle-end callback must not be clobbered by it — e.g. the user picks
    /// "remind me later" on a found update, which still ends the cycle.
    @Test func cycleEndedAfterFoundLeavesAvailableAlone() {
        let (service, controller, _) = make()
        service.checkApp()
        controller.emit?(.found(version: "0.12.0"))
        #expect(service.appUpdate.state == .available)
        controller.emit?(.cycleEnded)
        #expect(service.appUpdate.state == .available)
        #expect(service.appUpdate.latestVersion == "0.12.0")
    }

    /// The no-update path fires both `updaterDidNotFindUpdate` (→
    /// `.upToDate`) and `didFinishUpdateCycleForUpdateCheck:` for the same
    /// cycle; `AppUpdater.cycleEndEvent` maps `SUNoUpdateError` back onto
    /// `.upToDate` too, so the second event must be a no-op, not clobber the
    /// first with `.cycleEnded`'s restore logic.
    @Test func upToDateThenCycleEndedStaysUpToDate() {
        let (service, controller, _) = make()
        service.checkApp()
        controller.emit?(.upToDate)
        #expect(service.appUpdate.state == .upToDate)
        controller.emit?(.cycleEnded)
        #expect(service.appUpdate.state == .upToDate)
    }

    // MARK: Plugin track results (ported; `applicable`/`releasesURL` renamed)

    @Test func pluginCheckAvailable() async {
        let (service, _, _) = make(plugin: PluginUpdateResult(
            applicable: true, installed: "0.7.2", latest: "0.8.0", isUpdateAvailable: true,
            releasesURL: URL(string: "https://github.com/example-org/scout-plugin/releases"), error: nil))
        // `make()` seeds a replaying subject, which already scheduled a launch
        // check against the same checker/result; settle it first so this
        // call's generation isn't racing that still-in-flight one.
        await service.pluginTask?.value
        await service.checkPlugin()
        #expect(service.pluginUpdate == UpdateStatus(currentVersion: "0.7.2", latestVersion: "0.8.0", state: .available))
        #expect(service.pluginReleasesURL?.absoluteString == "https://github.com/example-org/scout-plugin/releases")
        #expect(service.pluginRowVisible == true)
        #expect(service.availableCount == 1)
    }

    @Test func pluginCheckUpToDateAndError() async {
        let (upToDate, _, _) = make()
        await upToDate.pluginTask?.value
        await upToDate.checkPlugin()
        #expect(upToDate.pluginUpdate.state == .upToDate)

        let (errored, _, _) = make(plugin: PluginUpdateResult(
            applicable: true, installed: "0.7.2", latest: nil, isUpdateAvailable: false, releasesURL: nil, error: "offline"))
        await errored.pluginTask?.value
        await errored.checkPlugin()
        #expect(errored.pluginUpdate.state == .error("offline"))
        #expect(errored.pluginUpdate.currentVersion == "0.7.2")
    }

    @Test func pluginNotInstalledStaysIdleWithNoVersion() async {
        let (service, _, _) = make(plugin: PluginUpdateResult(
            applicable: true, installed: nil, latest: nil, isUpdateAvailable: false, releasesURL: nil, error: nil))
        await service.pluginTask?.value
        await service.checkPlugin()
        #expect(service.pluginUpdate.currentVersion == nil)
        #expect(service.pluginUpdate.state == .idle)   // nothing to report, but the engine is eligible so the row stays
        #expect(service.pluginRowVisible == true)
    }

    @Test func bothTracksCountTowardTheBadge() async {
        let (service, controller, _) = make(plugin: PluginUpdateResult(
            applicable: true, installed: "0.7.2", latest: "0.8.0", isUpdateAvailable: true, releasesURL: nil, error: nil))
        await service.pluginTask?.value
        await service.checkPlugin()
        controller.emit?(.found(version: "0.12.0"))
        #expect(service.availableCount == 2)
    }

    // MARK: Engine-state gating (new)

    @Test func managedEngineHidesThePluginRowWithoutChecking() async {
        let managed = install(named: ".scout/engine/current")
        let subject = PassthroughSubject<EngineState, Never>()
        let checker = FakePluginChecker(result: .notApplicable)
        let (service, _) = makeService(checker: checker, engineStates: subject.eraseToAnyPublisher())

        subject.send(.managed(managed, vaultBootstrapped: true))
        await service.pluginTask?.value

        #expect(service.pluginRowVisible == false)
        #expect(service.availableCount == 0)
        let calls = await checker.calls
        #expect(calls == [.managed(managed, vaultBootstrapped: true)])
    }

    @Test func externalEngineShowsTheRow() async {
        let subject = PassthroughSubject<EngineState, Never>()
        let checker = FakePluginChecker(result: PluginUpdateResult(
            applicable: true, installed: "0.7.2", latest: "0.8.0", isUpdateAvailable: true,
            releasesURL: URL(string: "https://github.com/example-org/scout-plugin/releases"), error: nil))
        let (service, _) = makeService(checker: checker, engineStates: subject.eraseToAnyPublisher())

        subject.send(.external(defaultInstall, .marketplaceCache))
        await service.pluginTask?.value

        #expect(service.pluginRowVisible == true)
        #expect(service.availableCount == 1)
        #expect(service.pluginReleasesURL?.absoluteString == "https://github.com/example-org/scout-plugin/releases")
    }

    @Test func engineStateChangeRechecks() async {
        let installA = install(named: "scout-plugin-a")
        let installB = install(named: "scout-plugin-b")
        let subject = PassthroughSubject<EngineState, Never>()
        let checker = FakePluginChecker(result: .notApplicable)
        let (service, _) = makeService(checker: checker, engineStates: subject.eraseToAnyPublisher())

        subject.send(.external(installA, .marketplaceCache))
        await service.pluginTask?.value
        subject.send(.external(installA, .marketplaceCache))   // same root → no recheck
        await service.pluginTask?.value
        subject.send(.external(installB, .marketplaceCache))   // different root → recheck
        await service.pluginTask?.value

        let calls = await checker.calls
        #expect(calls.count == 2)
    }

    @Test func periodicRefreshWithSameEngineDoesNotRefetch() async {
        let subject = PassthroughSubject<EngineState, Never>()
        let checker = FakePluginChecker(result: .notApplicable)
        let (service, _) = makeService(checker: checker, engineStates: subject.eraseToAnyPublisher())

        subject.send(.external(defaultInstall, .marketplaceCache))
        await service.pluginTask?.value
        subject.send(.external(defaultInstall, .marketplaceCache))
        await service.pluginTask?.value
        subject.send(.external(defaultInstall, .marketplaceCache))
        await service.pluginTask?.value

        let calls = await checker.calls
        #expect(calls.count == 1)
    }

    @Test func switchingToManagedHidesTheRow() async {
        let managed = install(named: ".scout/engine/current")
        let subject = PassthroughSubject<EngineState, Never>()
        let checker = FakePluginChecker(results: [
            PluginUpdateResult(applicable: true, installed: "0.7.2", latest: "0.8.0", isUpdateAvailable: true, releasesURL: nil, error: nil),
            .notApplicable,
        ])
        let (service, _) = makeService(checker: checker, engineStates: subject.eraseToAnyPublisher())

        subject.send(.external(defaultInstall, .marketplaceCache))
        await service.pluginTask?.value
        #expect(service.pluginRowVisible == true)
        #expect(service.availableCount == 1)

        subject.send(.managed(managed, vaultBootstrapped: true))
        await service.pluginTask?.value
        #expect(service.pluginRowVisible == false)
        #expect(service.availableCount == 0)
    }

    @Test func checkPluginBeforeAnyEngineStateIsANoOp() async {
        let subject = PassthroughSubject<EngineState, Never>()   // never emits
        let checker = FakePluginChecker(result: .notApplicable)
        let (service, _) = makeService(checker: checker, engineStates: subject.eraseToAnyPublisher())

        await service.checkPlugin()

        let calls = await checker.calls
        #expect(calls.isEmpty)
        #expect(service.pluginUpdate == UpdateStatus())
        #expect(service.pluginRowVisible == false)
    }

    /// Regression for the reviewer's finding: `pluginTask?.cancel()` in
    /// `check(_:)` only flips the task's cancellation flag — it can't stop a
    /// non-throwing `PluginUpdateChecking.check(engine:)` that's already
    /// mid-await. If the superseded (first) check resolves *after* the
    /// current (second) one, it must not overwrite the current result.
    @Test func overlappingChecksApplyOnlyTheLatestResult() async {
        let installA = install(named: "scout-plugin-a")
        let installB = install(named: "scout-plugin-b")
        let subject = PassthroughSubject<EngineState, Never>()
        let checker = GatedPluginChecker(results: [
            // index 0 — for installA; suspends until release(); stale by the
            // time it resolves.
            PluginUpdateResult(applicable: true, installed: "0.7.2", latest: "0.8.0",
                               isUpdateAvailable: true, releasesURL: nil, error: nil),
            // index 1 — for installB; resolves immediately and is current.
            PluginUpdateResult(applicable: true, installed: "0.9.0", latest: "0.9.0",
                               isUpdateAvailable: false, releasesURL: nil, error: nil),
        ])
        let (service, _) = makeService(checker: checker, engineStates: subject.eraseToAnyPublisher())

        subject.send(.external(installA, .marketplaceCache))
        let supersededTask = service.pluginTask   // captured before it's overwritten below

        subject.send(.external(installB, .marketplaceCache))   // engine changed again before A resolved
        await service.pluginTask?.value   // the second (current) check completes — it never suspends

        #expect(service.pluginUpdate.currentVersion == "0.9.0")
        #expect(service.pluginUpdate.state == .upToDate)
        #expect(service.availableCount == 0)

        await checker.release()          // now let the first (superseded) check resolve
        await supersededTask?.value       // deterministically wait for it to finish discarding its result

        // The stale, superseded result must not have overwritten the current one.
        #expect(service.pluginUpdate.currentVersion == "0.9.0")
        #expect(service.pluginUpdate.state == .upToDate)
        #expect(service.availableCount == 0)
    }
}
