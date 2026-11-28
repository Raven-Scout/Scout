import Testing
import Foundation
import Combine
@testable import Scout

/// `AppState` re-publishes only the child-service values the shell shows
/// (badges, dots, the window gate), each when it changes. Every other change
/// reaches only the views that observe that service (`Observing`), so a usage
/// refresh no longer redraws every view holding `AppState` and every scene of
/// `ScoutApp.body`. Built from `.testing(...)`, never `.production()`.
@MainActor
@Suite("AppState observation")
struct AppStateObservationTests {
    private func tempDirectory() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("AppStateObservationTests-\(UUID().uuidString)", isDirectory: true)
    }

    private let install = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"),
                                        python: nil, version: "0.10.0", vault: nil)

    /// A service publishing without changing a badge, dot or gate leaves
    /// `AppState` quiet — the usage summary and the run ledger above all,
    /// which refresh on every run and every few minutes.
    @Test func serviceChangesDoNotRepublishAppState() async throws {
        let tmp = tempDirectory()
        defer { try? FileManager.default.removeItem(at: tmp) }
        let appState = AppState(configuration: .testing(scoutDirectory: tmp))
        // Let init's own one-shot main-queue hops (each `$value` sink's first
        // emission) land before counting: they are not a service change.
        try await Task.sleep(for: .milliseconds(300))

        var fired = 0
        let cancellable = appState.objectWillChange.sink { _ in fired += 1 }
        defer { cancellable.cancel() }

        appState.scoutUsageService.objectWillChange.send()
        appState.runLedgerService.objectWillChange.send()
        appState.budgetSettingsService.objectWillChange.send()
        appState.wishlistDocumentService.objectWillChange.send()
        appState.researchDocumentService.objectWillChange.send()
        appState.engineHealth.objectWillChange.send()
        appState.sessionLogService.objectWillChange.send()
        appState.scheduleService.objectWillChange.send()
        appState.connectorHealthService.objectWillChange.send()
        // The old forwarding hopped through the main queue: give it the tick.
        try await Task.sleep(for: .milliseconds(100))

        #expect(fired == 0)
    }

    /// The window gate and the Settings dot follow the engine state, live:
    /// a refresh that finds no engine gates the tabs.
    @Test func engineGateAndDotFollowTheEngineState() async {
        let tmp = tempDirectory()
        defer { try? FileManager.default.removeItem(at: tmp) }
        var configuration = AppState.Configuration.testing(scoutDirectory: tmp)
        configuration.initialEngineState = .external(install, .devCheckout)
        let appState = AppState(configuration: configuration)
        #expect(!appState.engineGatesTabs)
        #expect(appState.engineNeedsAttention == appState.engineHealth.needsAttention)

        var fired = false
        let cancellable = appState.objectWillChange.sink { _ in fired = true }
        defer { cancellable.cancel() }

        // `.testing(...)`'s engine layout is an empty temp home: `.notInstalled`.
        await appState.engineHealth.refresh()

        #expect(appState.engineHealth.state == .notInstalled)
        #expect(appState.engineGatesTabs)
        #expect(appState.engineNeedsAttention)
        #expect(fired)
        #expect(MainWindowView.showsOnboarding(gatesTabs: appState.engineGatesTabs, onboardingActive: false))
    }

    @Test func gatedInitialStateIsThereFromTheFirstFrame() {
        let tmp = tempDirectory()
        var configuration = AppState.Configuration.testing(scoutDirectory: tmp)
        configuration.initialEngineState = .notInstalled
        let appState = AppState(configuration: configuration)
        #expect(appState.engineGatesTabs)
        #expect(appState.engineNeedsAttention)
    }

    /// The Wishlist and Research badges follow their documents' items.
    @Test func perFileBadgesFollowTheirDocuments() throws {
        let tmp = tempDirectory()
        defer { try? FileManager.default.removeItem(at: tmp) }
        for (config, name, status) in [(PerFileTabConfig.wishlist, "2026-06-15-batch-the-digest.md", "open"),
                                       (.wishlist, "2026-06-14-old-idea.md", "done"),
                                       (.research, "2026-06-14-tracing-costs.md", "in-progress")] {
            let dir = tmp.appendingPathComponent(config.directoryDefaultRelative, isDirectory: true)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try "---\ntitle: Item\nstatus: \(status)\n---\n\n# Item\n"
                .write(to: dir.appendingPathComponent(name), atomically: true, encoding: .utf8)
        }
        let state = AppState(configuration: .testing(scoutDirectory: tmp))
        #expect(state.wishlistActiveCount == 0)
        #expect(state.researchActiveCount == 0)

        state.wishlistDocumentService.load()
        state.researchDocumentService.load()

        #expect(state.wishlistDocumentService.items.count == 2)
        #expect(state.wishlistActiveCount == 1)
        #expect(state.researchActiveCount == 1)
    }

    @Test func healthDotIsRedRunHealthOnly() {
        #expect(!RunLedgerService.attentionNeeded(.loading))
        #expect(!RunLedgerService.attentionNeeded(.failed("no engine")))
        #expect(!RunLedgerService.attentionNeeded(.unsupported("old engine")))
    }

    /// `Observing`'s relay fires for each object it was given, and only those.
    @Test func changeRelayFollowsItsObjectsOnly() {
        let a = ObservableObjectPublisherBox(), b = ObservableObjectPublisherBox(), other = ObservableObjectPublisherBox()
        let relay = ChangeRelay([a, b])
        var fired = 0
        let cancellable = relay.objectWillChange.sink { _ in fired += 1 }
        defer { cancellable.cancel() }

        a.objectWillChange.send()
        b.objectWillChange.send()
        other.objectWillChange.send()

        #expect(fired == 2)
    }

    /// `DayTick` publishes on a day change, a time-zone change and a clock
    /// change, so "today" moves at midnight in a window left open overnight.
    @Test func dayTickFollowsTheCalendar() async throws {
        let center = NotificationCenter()
        let tick = DayTick(center: center)
        var fired = 0
        let cancellable = tick.objectWillChange.sink { _ in fired += 1 }
        defer { cancellable.cancel() }

        for name in [Notification.Name.NSCalendarDayChanged, .NSSystemTimeZoneDidChange, .NSSystemClockDidChange] {
            center.post(name: name, object: nil)
        }
        center.post(name: Notification.Name("SomethingElse"), object: nil)
        // It hops to the main queue: give it the tick.
        try await Task.sleep(for: .milliseconds(100))

        #expect(fired == 3)
    }

    /// `MinuteTick` publishes on its own, every interval, with nothing else changing.
    @Test func minuteTickPublishesOnItsOwn() async throws {
        let tick = MinuteTick(every: 0.05)
        var fired = 0
        let cancellable = tick.objectWillChange.sink { _ in fired += 1 }
        defer { cancellable.cancel() }

        try await Task.sleep(for: .milliseconds(400))

        #expect(fired >= 2)
    }
}

private final class ObservableObjectPublisherBox: ObservableObject {}
