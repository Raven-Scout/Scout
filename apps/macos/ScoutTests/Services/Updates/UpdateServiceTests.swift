import Testing
import Foundation
import Combine
@testable import Scout

@MainActor
private final class FakeAppController: AppUpdateController {
    let isEnabled: Bool
    let currentVersion: String? = "0.11.2"
    var checkCalls = 0
    var emit: (@MainActor (AppUpdateEvent) -> Void)?
    init(isEnabled: Bool) { self.isEnabled = isEnabled }
    func checkForUpdates() { checkCalls += 1 }
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
        checker: FakePluginChecker,
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

    // MARK: Plugin track results (ported; `applicable`/`releasesURL` renamed)

    @Test func pluginCheckAvailable() async {
        let (service, _, _) = make(plugin: PluginUpdateResult(
            applicable: true, installed: "0.7.2", latest: "0.8.0", isUpdateAvailable: true,
            releasesURL: URL(string: "https://github.com/example-org/scout-plugin/releases"), error: nil))
        await service.checkPlugin()
        #expect(service.pluginUpdate == UpdateStatus(currentVersion: "0.7.2", latestVersion: "0.8.0", state: .available))
        #expect(service.pluginReleasesURL?.absoluteString == "https://github.com/example-org/scout-plugin/releases")
        #expect(service.pluginRowVisible == true)
        #expect(service.availableCount == 1)
    }

    @Test func pluginCheckUpToDateAndError() async {
        let (upToDate, _, _) = make()
        await upToDate.checkPlugin()
        #expect(upToDate.pluginUpdate.state == .upToDate)

        let (errored, _, _) = make(plugin: PluginUpdateResult(
            applicable: true, installed: "0.7.2", latest: nil, isUpdateAvailable: false, releasesURL: nil, error: "offline"))
        await errored.checkPlugin()
        #expect(errored.pluginUpdate.state == .error("offline"))
        #expect(errored.pluginUpdate.currentVersion == "0.7.2")
    }

    @Test func pluginNotInstalledStaysIdleWithNoVersion() async {
        let (service, _, _) = make(plugin: PluginUpdateResult(
            applicable: true, installed: nil, latest: nil, isUpdateAvailable: false, releasesURL: nil, error: nil))
        await service.checkPlugin()
        #expect(service.pluginUpdate.currentVersion == nil)
        #expect(service.pluginUpdate.state == .idle)   // nothing to report, but the engine is eligible so the row stays
        #expect(service.pluginRowVisible == true)
    }

    @Test func bothTracksCountTowardTheBadge() async {
        let (service, controller, _) = make(plugin: PluginUpdateResult(
            applicable: true, installed: "0.7.2", latest: "0.8.0", isUpdateAvailable: true, releasesURL: nil, error: nil))
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
}
