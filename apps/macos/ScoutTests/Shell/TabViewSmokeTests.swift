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
        await vault.state.actionItemsDocumentService.load(date: Self.fixtureDay)
        // Without this the test silently renders the missing-day chrome —
        // the same render as `actionItemsRendersMissingDay` — and the
        // populated card pipeline is never evaluated.
        guard case .loaded = vault.state.actionItemsDocumentService.state else {
            Issue.record("fixture day did not load: \(vault.state.actionItemsDocumentService.state)")
            return
        }

        // Without this the test passes on the *missing-day* chrome whenever the
        // fixture filename drifts from `ActionItemsDocumentService.url(for:)`,
        // which is exactly what it is meant to catch. `sections` must be
        // non-empty too: a loaded-but-empty document renders the same chrome.
        let state = vault.state.actionItemsDocumentService.state
        guard case .loaded(let document) = state else {
            Issue.record("expected .loaded after load(date:), got \(state)")
            return
        }
        #expect(!document.sections.isEmpty)

        ViewHost.render(
            ActionItemsView(
                scoutDirectory: vault.state.scoutDirectory,
                actionItemsDirectory: vault.state.actionItemsDirectory)
                .environmentObject(vault.state.actionItemsDocumentService)
                .environmentObject(vault.state.actionItemsWriterBox)
                .environmentObject(vault.state.actionItemsEnvState)
                .environmentObject(vault.state.displayProfileService)
                .environmentObject(vault.state.vaultUndo))
    }

    @Test("the action items tab renders its missing-day state")
    func actionItemsRendersMissingDay() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        // A day with no file on disk — the empty/missing chrome.
        await vault.state.actionItemsDocumentService.load(
            date: Date(timeIntervalSince1970: 1_600_000_000))

        ViewHost.render(
            ActionItemsView(
                scoutDirectory: vault.state.scoutDirectory,
                actionItemsDirectory: vault.state.actionItemsDirectory)
                .environmentObject(vault.state.actionItemsDocumentService)
                .environmentObject(vault.state.actionItemsWriterBox)
                .environmentObject(vault.state.actionItemsEnvState)
                .environmentObject(vault.state.displayProfileService)
                .environmentObject(vault.state.vaultUndo))
    }

    // MARK: - Changes

    @Test("the changes tab renders a loaded snapshot")
    func changesRenders() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let service = try await LoadedChangesService.make()
        // Without this the test renders the loading chrome and the digest card is never evaluated.
        guard case .loaded = service.state else {
            Issue.record("changes fixtures did not load: \(service.state)")
            return
        }

        ViewHost.render(
            ChangesView()
                .environmentObject(service)
                .environmentObject(LoadedChangesService.decisions())
                .environmentObject(vault.state.vaultUndo))
    }

    // MARK: - Research

    @Test("the research tab renders")
    func researchRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        vault.state.researchDocumentService.load()

        ViewHost.render(
            PerFileListView(config: .research)
                .environmentObject(vault.state.researchDocumentService)
                .environmentObject(vault.state.perFileWriterBox)
                .environmentObject(vault.state.vaultUndo))
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
                .environmentObject(vault.state.knowledgeBaseWriterBox)
                .environmentObject(vault.state.vaultUndo))
    }

    // MARK: - Schedules

    @Test("the schedules tab renders")
    func schedulesRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(
            SchedulesView()
                .environmentObject(vault.state.scheduleEditService)
                .environmentObject(vault.state.modelSettingsService)
                .environmentObject(vault.state.vaultUndo)
                .environmentObject(vault.state))
    }

    // MARK: - Control Center

    @Test("the control center renders")
    func controlCenterRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(ControlCenterView().environmentObject(vault.state))
    }

    @Test("the repo-state rail card renders the vault the app is looking at")
    func repoStateRailCardRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(
            RepoStateRailCard().environmentObject(vault.state).frame(width: 280),
            size: CGSize(width: 280, height: 160))
    }

    @Test("the repo-state path is ~-abbreviated only under the home directory")
    func repoStatePathAbbreviation() {
        let home = URL(fileURLWithPath: "/Users/alex")
        #expect(RepoStateRailCard.displayPath(URL(fileURLWithPath: "/Users/alex/Scout"), home: home) == "~/Scout")
        #expect(RepoStateRailCard.displayPath(URL(fileURLWithPath: "/Users/alex/Vaults/Work"), home: home) == "~/Vaults/Work")
        #expect(RepoStateRailCard.displayPath(home, home: home) == "~")
        #expect(RepoStateRailCard.displayPath(URL(fileURLWithPath: "/Users/alexander/Scout"), home: home) == "/Users/alexander/Scout")
        #expect(RepoStateRailCard.displayPath(URL(fileURLWithPath: "/Volumes/Data/Scout"), home: home) == "/Volumes/Data/Scout")
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
