import AppKit
import SwiftUI
import Testing
@testable import Scout

/// Renders each destination tab against a populated temp vault. These are the
/// views the app only ever built when a user clicked into the tab — a trap in
/// any of their body builders previously shipped undetected.
@MainActor
@Suite("View smoke — tabs", .serialized)
struct TabViewSmokeTests {

    // MARK: - Action Items

    @Test("the action items tab renders a populated day")
    func actionItemsRenders() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        try await vault.state.actionItemsDocumentService.load(date: Self.fixtureDay)

        ViewHost.render(
            ActionItemsView(
                scoutDirectory: vault.state.scoutDirectory,
                actionItemsDirectory: vault.state.actionItemsDirectory)
                .environmentObject(vault.state.actionItemsDocumentService)
                .environmentObject(vault.state.actionItemsWriterBox)
                .environmentObject(vault.state.actionItemsEnvState))
    }

    @Test("the action items tab renders its missing-day state")
    func actionItemsRendersMissingDay() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        // A day with no file on disk — the empty/missing chrome.
        try? await vault.state.actionItemsDocumentService.load(
            date: Date(timeIntervalSince1970: 1_600_000_000))

        ViewHost.render(
            ActionItemsView(
                scoutDirectory: vault.state.scoutDirectory,
                actionItemsDirectory: vault.state.actionItemsDirectory)
                .environmentObject(vault.state.actionItemsDocumentService)
                .environmentObject(vault.state.actionItemsWriterBox)
                .environmentObject(vault.state.actionItemsEnvState))
    }

    // MARK: - Proposals

    @Test("the proposals tab renders a pending proposal")
    func proposalsRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        vault.state.proposalsDocumentService.load()

        ViewHost.render(
            ProposalsView()
                .environmentObject(vault.state.proposalsDocumentService)
                .environmentObject(vault.state.proposalsWriterBox))
    }

    // MARK: - Wishlist / Research

    @Test("the wishlist tab renders")
    func wishlistRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        vault.state.wishlistDocumentService.load()

        ViewHost.render(
            PerFileListView(config: .wishlist)
                .environmentObject(vault.state.wishlistDocumentService)
                .environmentObject(vault.state.perFileWriterBox))
    }

    @Test("the research tab renders")
    func researchRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        vault.state.researchDocumentService.load()

        ViewHost.render(
            PerFileListView(config: .research)
                .environmentObject(vault.state.researchDocumentService)
                .environmentObject(vault.state.perFileWriterBox))
    }

    // MARK: - Knowledge Base

    @Test("the knowledge base tab renders a loaded tree")
    func knowledgeBaseRenders() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        vault.state.knowledgeBaseService.load()
        await vault.state.knowledgeBaseService.reparseAndWait()

        ViewHost.render(
            KnowledgeBaseView()
                .environmentObject(vault.state.knowledgeBaseService)
                .environmentObject(vault.state.knowledgeBaseWriterBox))
    }

    // MARK: - Schedules

    @Test("the schedules tab renders")
    func schedulesRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(
            SchedulesView()
                .environmentObject(vault.state.scheduleEditService)
                .environmentObject(vault.state))
    }

    // MARK: - Control Center

    @Test("the control center renders")
    func controlCenterRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(ControlCenterView().environmentObject(vault.state))
    }

    // MARK: - helpers

    /// The day the fixture action-items file is written for.
    private static var fixtureDay: Date {
        var comps = DateComponents()
        comps.year = 2026; comps.month = 6; comps.day = 15
        comps.hour = 12
        return Calendar(identifier: .gregorian).date(from: comps)!
    }
}
