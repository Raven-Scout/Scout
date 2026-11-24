import AppKit
import Foundation
import SwiftUI
import Testing
@testable import Scout

/// Every page follows a change to its own data while it is on screen.
///
/// `AppState` re-publishes no child service's changes: each view observes the
/// services it shows (`Observing`, its own `@EnvironmentObject`s) or a derived
/// `AppState` value (badges, dots, the window gate). The failure mode of that
/// narrowing is a page that renders right once and then stops updating, which
/// a fresh render can't catch. So each test keeps the real window hosted
/// (`MainWindowView` wires every page the way the app does), changes the data
/// the page reads through its real service, and reads the page again.
@MainActor
@Suite("Pages follow their own data live", .serialized)
struct PageLiveUpdateTests {

    // MARK: - Shell (AppState's derived values)

    @Test("the sidebar's Wishlist badge follows the wishlist")
    func wishlistBadge() async throws {
        let vault = try LiveVault(); defer { vault.tearDown() }
        let tree = await LiveAccessibilityTree(vault.window(.controlCenter)); defer { tree.close() }
        let row = SidebarItem.wishlist.accessibilityIdentifier
        let badge = SidebarItem.wishlist.badgeDescription(1)
        let before = await tree.nodes { $0.element(row) != nil }
        #expect(before.element(row)?.value.contains(badge) == false)

        try vault.writePerFileItem(.wishlist, "2026-06-15-batch-the-digest.md", status: "open")
        vault.state.wishlistDocumentService.load()

        let after = await tree.nodes { $0.element(row)?.value.contains(badge) == true }
        #expect(after.element(row)?.value.contains(badge) == true, "\(after.element(row).map(\.description) ?? "no row")")
    }

    @Test("the sidebar's Health dot follows the run ledger")
    func healthDot() async throws {
        let vault = try LiveVault(); defer { vault.tearDown() }
        let tree = await LiveAccessibilityTree(vault.window(.controlCenter)); defer { tree.close() }
        let row = SidebarItem.health.accessibilityIdentifier
        let before = await tree.nodes { $0.element(row) != nil }
        #expect(before.element(row)?.value.contains("needs attention") == false)

        vault.script.ledger = try LiveVault.fixture("run-ledger")
        await vault.state.runLedgerService.refresh()
        #expect(vault.state.runLedgerService.needsAttention, "the fixture's run health is red")

        let after = await tree.nodes { $0.element(row)?.value.contains("needs attention") == true }
        #expect(after.element(row)?.value.contains("needs attention") == true, "\(after.element(row).map(\.description) ?? "no row")")
    }

    @Test("an engine check that finds no engine gates the pages and marks Settings")
    func engineHealthGate() async throws {
        let vault = try LiveVault(); defer { vault.tearDown() }
        let tree = await LiveAccessibilityTree(vault.window(.controlCenter)); defer { tree.close() }
        let settings = SidebarView.settingsRowIdentifier
        let page = SidebarItem.schedules.accessibilityIdentifier
        let gated = "unavailable until Scout is set up"
        let before = await tree.nodes { $0.element(page) != nil }
        #expect(before.element(page)?.value.contains(gated) == false)

        // `.testing(...)`'s engine layout is an empty temp home: `.notInstalled`.
        await vault.state.engineHealth.refresh()
        #expect(vault.state.engineHealth.state == .notInstalled)

        let after = await tree.nodes {
            $0.element(page)?.value.contains(gated) == true
                && $0.element(settings)?.value.contains("needs attention") == true
        }
        #expect(after.element(page)?.value.contains(gated) == true, "\(after.element(page).map(\.description) ?? "no row")")
        #expect(after.element(settings)?.value.contains("needs attention") == true,
                "\(after.element(settings).map(\.description) ?? "no row")")
    }

    @Test("the status bar's cost follows the usage summary")
    func statusBarCost() async throws {
        let vault = try LiveVault(); defer { vault.tearDown() }
        let tree = await LiveAccessibilityTree(vault.window(.controlCenter)); defer { tree.close() }

        vault.script.usage = try LiveVault.fixture("usage-summary")
        await vault.state.scoutUsageService.refresh()
        let today = try #require(vault.state.scoutUsageService.summary?.today)
        let cost = UsageFormat.shortCost(today)

        let after = await tree.nodes { $0.shows(cost) }
        #expect(after.shows(cost), "no \(cost) on screen")
    }

    // MARK: - Pages

    @Test("Control Center shows the run-health banner once the ledger turns red")
    func controlCenterRunHealthBanner() async throws {
        let vault = try LiveVault(); defer { vault.tearDown() }
        let tree = await LiveAccessibilityTree(vault.window(.controlCenter)); defer { tree.close() }
        let before = await tree.nodes { $0.element(SidebarItem.controlCenter.accessibilityIdentifier) != nil }
        #expect(!before.shows("Open Health"))

        vault.script.ledger = try LiveVault.fixture("run-ledger")
        await vault.state.runLedgerService.refresh()
        let service = vault.state.runLedgerService
        try #require(RunOutcomeDisplay.bannerProblem(service.health, firstSeen: service.firstSeen, now: Date()) != nil,
                     "the fixture must raise the banner for this test to mean anything")

        let after = await tree.nodes { $0.shows("Open Health") }
        #expect(after.shows("Open Health"))
    }

    @Test("Health shows the run ledger once it loads")
    func healthPageLedger() async throws {
        let vault = try LiveVault(); defer { vault.tearDown() }
        let tree = await LiveAccessibilityTree(vault.window(.health)); defer { tree.close() }
        // The page's own first read fails (the runner has no ledger yet).
        let before = await tree.nodes { $0.shows("Try again") }
        #expect(before.shows("Try again"))

        vault.script.ledger = try LiveVault.fixture("run-ledger")
        await vault.state.runLedgerService.refresh()
        let headline = HealthPanelModel(ledger: vault.state.runLedgerService.ledger,
                                        doctor: vault.state.engineHealth.doctor).headline

        let after = await tree.nodes { $0.shows(headline) }
        #expect(after.shows(headline), "no \"\(headline)\" on screen")
    }

    @Test("Schedules shows a slot added to the schedule")
    func schedulesEdit() async throws {
        let vault = try LiveVault(); defer { vault.tearDown() }
        let service = vault.state.scheduleEditService
        vault.script.slots = LiveVault.slotList(["morning-briefing"])
        let tree = await LiveAccessibilityTree(vault.window(.schedules)); defer { tree.close() }
        try await service.loadAll()
        // The name the list draws, in its table rows and its stacked cards alike.
        let morning = try #require(service.slots.first { $0.key == "morning-briefing" }).displayName
        let first = await tree.nodes { $0.shows(morning) }
        #expect(first.shows(morning),
                "no \"\(morning)\" on screen:\n\(first.map(\.description).joined(separator: "\n").prefix(4000))")

        vault.script.slots = LiveVault.slotList(["morning-briefing", "evening-dreaming"])
        try await service.loadAll()
        let evening = try #require(service.slots.first { $0.key == "evening-dreaming" }).displayName
        #expect(!first.shows(evening))

        let after = await tree.nodes { $0.shows(evening) }
        #expect(after.shows(evening), "no \"\(evening)\" on screen")
    }

    @Test("Action Items shows a task added to today's file")
    func actionItemsParse() async throws {
        let vault = try LiveVault(); defer { vault.tearDown() }
        let service = vault.state.actionItemsDocumentService
        try vault.writeActionItems(tasks: ["Reply to Priya about the RFC"])
        let tree = await LiveAccessibilityTree(vault.window(.actionItems)); defer { tree.close() }
        await service.load(date: Date())
        let first = await tree.nodes { $0.shows("Reply to Priya about the RFC") }
        #expect(first.shows("Reply to Priya about the RFC"))
        #expect(!first.shows("Water the office plants"))

        try vault.writeActionItems(tasks: ["Reply to Priya about the RFC", "Water the office plants"])
        await service.load(date: Date())

        let after = await tree.nodes { $0.shows("Water the office plants") }
        #expect(after.shows("Water the office plants"))
    }

    @Test("Knowledge Base shows a note added to the vault")
    func knowledgeBaseChange() async throws {
        let vault = try LiveVault(); defer { vault.tearDown() }
        let kb = vault.state.knowledgeBaseService
        try vault.writeNote("alpha-runbook")
        let tree = await LiveAccessibilityTree(vault.window(.knowledgeBase)); defer { tree.close() }
        kb.load()
        await kb.reparseAndWait()
        let first = await tree.nodes { $0.shows("alpha-runbook") }
        #expect(first.shows("alpha-runbook"))
        #expect(!first.shows("zebra-runbook"))

        try vault.writeNote("zebra-runbook")
        await kb.reparseAndWait()
        #expect(kb.tree.contains { $0.displayName == "zebra-runbook" })

        let after = await tree.nodes { $0.shows("zebra-runbook") }
        #expect(after.shows("zebra-runbook"))
    }
}

/// Answers the engine's commands from values a test swaps mid-test, so a page
/// can be shown one state and then the next through its real service.
/// A `nil` answer fails to launch, the way a missing engine does.
private final class LiveScript: @unchecked Sendable {
    private let lock = NSLock()
    private var _ledger: String?
    private var _usage: String?
    private var _slots = "[]"

    var ledger: String? { get { lock.withLock { _ledger } } set { lock.withLock { _ledger = newValue } } }
    var usage: String? { get { lock.withLock { _usage } } set { lock.withLock { _usage = newValue } } }
    var slots: String { get { lock.withLock { _slots } } set { lock.withLock { _slots = newValue } } }

    static func answer(_ stdout: String?) throws -> ProcessResult {
        guard let stdout else {
            throw NSError(domain: NSPOSIXErrorDomain, code: 2, userInfo: [NSLocalizedDescriptionKey: "no engine yet"])
        }
        return ProcessResult(exitCode: 0, stdout: Data(stdout.utf8), stderr: Data())
    }
}

/// A small vault plus an `AppState` whose engine answers from a `LiveScript`.
/// Names and content are invented (see the app's CLAUDE.md on fixtures).
@MainActor
private struct LiveVault {
    let root: URL
    let state: AppState
    let updates: UpdateService
    let script: LiveScript

    init() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("live-vault-\(UUID().uuidString)")
            .resolvingSymlinksInPath()
        for sub in ["action-items", "knowledge-base", ".scout-logs", ".scout-state", ".scout-cache"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(sub),
                                                    withIntermediateDirectories: true)
        }
        let runner = RuleBasedRunner()
        let script = LiveScript()
        self.script = script
        func command(_ prefix: [String]) -> (URL, [String]) -> Bool {
            { url, args in url.lastPathComponent == "false" && Array(args.prefix(prefix.count)) == prefix }
        }
        runner.on(command(["runs"])) { _, _, _ in try LiveScript.answer(script.ledger) }
        runner.on(command(["budget", "usage"])) { _, _, _ in try LiveScript.answer(script.usage) }
        runner.on(command(["schedule", "list"])) { _, _, _ in try LiveScript.answer(script.slots) }
        var configuration = AppState.Configuration.testing(scoutDirectory: root, runner: runner)
        // An engine that backs the pages until a test's own check says otherwise.
        let install = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"),
                                    python: nil, version: "0.10.0", vault: nil)
        configuration.initialEngineState = .external(install, .devCheckout)
        state = AppState(configuration: configuration)
        updates = SmokeVault.inertUpdates()
    }

    func window(_ page: SidebarItem) -> some View {
        MainWindowView(initialSelection: page)
            .environmentObject(state)
            .environmentObject(state.proposalsDocumentService)
            .environmentObject(updates)
    }

    func tearDown() { try? FileManager.default.removeItem(at: root) }

    static func fixture(_ name: String) throws -> String {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: name, withExtension: "json"))
        return try String(contentsOf: url, encoding: .utf8)
    }

    static func slotList(_ keys: [String]) -> String {
        let rows = keys.map { key in
            #"{"key":"\#(key)","type":"briefing","runner":"run-scout.sh","fires_at_local":"07:00","weekdays":["Mon","Tue","Wed","Thu","Fri"],"missed_window_hours":4,"on_miss":"fire","cooldown_minutes":60,"budget_usd":null,"tz":null,"runtime":"local"}"#
        }
        return "[" + rows.joined(separator: ",") + "]"
    }

    func writePerFileItem(_ config: PerFileTabConfig, _ name: String, status: String) throws {
        let dir = root.appendingPathComponent(config.directoryDefaultRelative, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try "---\ntitle: Batch the digest\nstatus: \(status)\n---\n\n# Batch the digest\n"
            .write(to: dir.appendingPathComponent(name), atomically: true, encoding: .utf8)
    }

    func writeActionItems(tasks: [String]) throws {
        let body = "# Action items\n\n## 🟡 To do\n" + tasks.map { "- [ ] \($0)\n" }.joined()
        try body.write(to: state.actionItemsDocumentService.url(for: Date()), atomically: true, encoding: .utf8)
    }

    func writeNote(_ name: String) throws {
        try "---\ntype: topic\n---\n# \(name)\nA note.\n"
            .write(to: root.appendingPathComponent("knowledge-base/\(name).md"), atomically: true, encoding: .utf8)
    }
}
