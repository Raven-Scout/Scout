import AppKit
import Foundation
import Testing
@testable import Scout

/// UI audit card R11: Settings is the ⌘, window only, split into tabs; its
/// path settings are choosers with validation; Authorship knows the owner's
/// name; and the wording uses the glossary's words.
@MainActor
@Suite("Settings window (R11)")
struct SettingsWindowTests {

    // MARK: - Tabs and deep links (#10, #82)

    @Test("every deep link lands on its tab")
    func anchorsSelectTheirTab() {
        #expect(SettingsTab(anchor: SettingsAnchor.engine) == .engine)
        #expect(SettingsTab(anchor: SettingsAnchor.updates) == .updates)
        #expect(SettingsTab(anchor: SettingsAnchor.authorship) == .general)
        #expect(SettingsTab(anchor: "settings-nowhere") == nil)
    }

    @Test("tab titles, symbols and identifiers are unique")
    func tabMetadata() {
        let titles = SettingsTab.allCases.map(\.title)
        let ids = SettingsTab.allCases.map(\.accessibilityIdentifier)
        #expect(Set(titles).count == titles.count && titles.allSatisfy { !$0.isEmpty })
        #expect(Set(ids).count == ids.count)
        #expect(SettingsTab.allCases.allSatisfy { NSImage(systemSymbolName: $0.systemImage, accessibilityDescription: nil) != nil })
    }

    /// R6's request window still works for a tab: a request made just now is
    /// seen by the Settings window that opens, and maps to its tab.
    @Test("a recent request opens Settings on the requested tab")
    func recentRequestPicksTheTab() {
        let now = Date()
        HelpActions.recordSettingsRequest(SettingsAnchor.updates, at: now)
        let anchor = HelpActions.settingsRequested(now: now.addingTimeInterval(1))
        #expect(anchor.flatMap(SettingsTab.init(anchor:)) == .updates)
        #expect(HelpActions.settingsRequested(now: now.addingTimeInterval(60)) == nil)
    }

    // MARK: - Path choosers (#82)

    private func tempHome() throws -> URL {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent("r11-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        return home
    }

    @Test("blank means the default and is always fine")
    func blankIsFine() throws {
        let home = try tempHome(); defer { try? FileManager.default.removeItem(at: home) }
        #expect(SettingsPathCheck.problem(for: "", kind: .folder, home: home) == nil)
        #expect(SettingsPathCheck.problem(for: "  ", kind: .executable, home: home) == nil)
    }

    @Test("a relative path is called out, not silently ignored")
    func relativePathIsAProblem() throws {
        let home = try tempHome(); defer { try? FileManager.default.removeItem(at: home) }
        #expect(SettingsPathCheck.url(for: "Scout", home: home) == nil)
        #expect(SettingsPathCheck.problem(for: "Vaults/Work", kind: .folder, home: home)?.contains("isn't a full path") == true)
        #expect(SettingsPathCheck.problem(for: "bin/claude", kind: .executable, home: home)?.contains("isn't a full path") == true)
    }

    @Test("folders must exist and be folders; ~ expands against home")
    func folderRules() throws {
        let home = try tempHome(); defer { try? FileManager.default.removeItem(at: home) }
        let folder = home.appendingPathComponent("Scout", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = home.appendingPathComponent("note.md")
        try Data("x".utf8).write(to: file)
        #expect(SettingsPathCheck.problem(for: "~/Scout", kind: .folder, home: home) == nil)
        #expect(SettingsPathCheck.problem(for: folder.path, kind: .folder, home: home) == nil)
        #expect(SettingsPathCheck.problem(for: "~/Missing", kind: .folder, home: home) == "This folder doesn't exist.")
        #expect(SettingsPathCheck.problem(for: "~/note.md", kind: .folder, home: home) == "This is a file, not a folder.")
    }

    @Test("the claude program must be a runnable file")
    func executableRules() throws {
        let home = try tempHome(); defer { try? FileManager.default.removeItem(at: home) }
        let tool = home.appendingPathComponent("claude")
        try Data("#!/bin/sh\n".utf8).write(to: tool)
        #expect(SettingsPathCheck.problem(for: tool.path, kind: .executable, home: home) == "This file can't be run as a program.")
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: tool.path)
        #expect(SettingsPathCheck.problem(for: tool.path, kind: .executable, home: home) == nil)
        #expect(SettingsPathCheck.problem(for: home.path, kind: .executable, home: home) == "This is a folder, not the claude program.")
        #expect(SettingsPathCheck.problem(for: "~/nope", kind: .executable, home: home) == "No file is at this path.")
    }

    @Test("a chosen folder under home is stored as ~/…, others as given")
    func storedText() throws {
        let home = URL(fileURLWithPath: "/Users/alex", isDirectory: true)
        #expect(SettingsPathCheck.storedText(for: URL(fileURLWithPath: "/Users/alex/Scout"), kind: .folder, home: home) == "~/Scout")
        #expect(SettingsPathCheck.storedText(for: URL(fileURLWithPath: "/Users/alex"), kind: .folder, home: home) == "~")
        #expect(SettingsPathCheck.storedText(for: URL(fileURLWithPath: "/Users/alexandra/Scout"), kind: .folder, home: home) == "/Users/alexandra/Scout")
        #expect(SettingsPathCheck.storedText(for: URL(fileURLWithPath: "/Volumes/Work/Scout"), kind: .folder, home: home) == "/Volumes/Work/Scout")
        // What it stores, AppState resolves to the same folder.
        #expect(SettingsPathCheck.url(for: "~/Scout", home: home)?.path == "/Users/alex/Scout")
    }

    /// #715 review (HIGH): a program is stored absolute, because
    /// `ClaudeLauncher.resolveClaudePath` takes the override as is — a saved
    /// `~/.local/bin/claude` made every Launch Claude say Claude is missing.
    @Test("a chosen claude program is stored absolute and resolves")
    func chosenProgramResolves() throws {
        let home = try tempHome(); defer { try? FileManager.default.removeItem(at: home) }
        let bin = home.appendingPathComponent(".local/bin", isDirectory: true)
        try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
        let tool = bin.appendingPathComponent("claude")
        try Data("#!/bin/sh\n".utf8).write(to: tool)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: tool.path)
        let stored = SettingsPathCheck.storedText(for: tool, kind: .executable, home: home)
        #expect(!stored.hasPrefix("~"))
        #expect(ClaudeLauncher.resolveClaudePath(override: stored, home: home) == tool.standardizedFileURL.path)
        // A `~/…` value saved before this fix still resolves.
        #expect(ClaudeLauncher.resolveClaudePath(override: "~/.local/bin/claude", home: home) == tool.path)
        #expect(ClaudeLauncher.resolveClaudePath(override: "~/.local/bin/missing", home: home) == nil)
    }

    @Test("a chosen Scout folder says when it isn't set up, or isn't where scheduled runs go")
    func scoutFolderNotes() throws {
        let home = try tempHome(); defer { try? FileManager.default.removeItem(at: home) }
        let folder = home.appendingPathComponent("Scout", isDirectory: true)
        let other = home.appendingPathComponent("Other", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: other, withIntermediateDirectories: true)
        #expect(SettingsPathCheck.scoutFolderNotes(for: "", engineVault: folder, home: home).isEmpty, "blank: the default")
        let empty = SettingsPathCheck.scoutFolderNotes(for: "~/Other", engineVault: folder, home: home)
        #expect(empty.count == 2)
        #expect(empty.first?.contains("no scout-config.yaml") == true)
        #expect(empty.last == "Scheduled runs still use ~/Scout.")
        try Data("version: 1\n".utf8).write(to: folder.appendingPathComponent("scout-config.yaml"))
        #expect(SettingsPathCheck.scoutFolderNotes(for: "~/Scout", engineVault: folder, home: home).isEmpty)
        #expect(SettingsPathCheck.scoutFolderNotes(for: "~/Missing", engineVault: folder, home: home).isEmpty,
                "a missing folder gets the problem line instead")
        // A symlinked Scout folder (say, into a synced folder) or one that
        // differs only in case is the same folder: no "scheduled runs" note.
        let link = home.appendingPathComponent("ScoutLink")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: folder)
        #expect(SettingsPathCheck.scoutFolderNotes(for: "~/ScoutLink", engineVault: folder, home: home).isEmpty)
        #expect(SettingsPathCheck.scoutFolderNotes(for: "~/Scout", engineVault: link, home: home).isEmpty)
        #expect(SettingsPathCheck.sameFolder(URL(fileURLWithPath: "/Users/alex/Scout"),
                                             URL(fileURLWithPath: "/Users/alex/scout")))
        #expect(!SettingsPathCheck.sameFolder(URL(fileURLWithPath: "/Users/alex/Scout"),
                                              URL(fileURLWithPath: "/Users/alex/Scout2")))
    }

    // MARK: - Authorship (#85)

    private func defaults() throws -> (UserDefaults, String) {
        let suite = "scout.tests.r11-author.\(UUID().uuidString)"
        return (try #require(UserDefaults(suiteName: suite)), suite)
    }

    @Test("an unset or blank name signs comments \"user\" and counts as not set")
    func unsetName() throws {
        let (d, suite) = try defaults(); defer { d.removePersistentDomain(forName: suite) }
        #expect(CommentAuthor.configuredName(d) == "user")
        #expect(!CommentAuthor.isNameSet(d))
        d.set("   ", forKey: CommentAuthor.defaultsKey)
        #expect(CommentAuthor.configuredName(d) == "user", "a cleared field means unset, not an empty byline")
        d.set("user", forKey: CommentAuthor.defaultsKey)
        #expect(!CommentAuthor.isNameSet(d), "the old default is still unset")
        d.set("Alex", forKey: CommentAuthor.defaultsKey)
        #expect(CommentAuthor.configuredName(d) == "Alex")
        #expect(CommentAuthor.isNameSet(d))
    }

    @Test("onboarding's name is adopted once, never over the owner's choice")
    func adoptOnce() throws {
        let (d, suite) = try defaults(); defer { d.removePersistentDomain(forName: suite) }
        #expect(!CommentAuthor.adoptOnboardingName("  ", into: d))
        #expect(CommentAuthor.adoptOnboardingName("Alex", into: d))
        #expect(!CommentAuthor.adoptOnboardingName("Priya", into: d))
        #expect(CommentAuthor.configuredName(d) == "Alex")
        // The inline-comment byline reads the same rule.
        #expect(ActionItemsDocumentService.inlineCommentAuthor(from: d) == "Alex")
    }

    // MARK: - Wording

    @Test("the Now strip says when the next run starts, without engine words")
    func nextRunLine() {
        let now = Date()
        let line = NowStripView.nextRunLine(now.addingTimeInterval(3 * 3600), now: now)
        #expect(line.hasPrefix("Starts "))
        #expect(!line.contains("dispatcher"))
        #expect(NowStripView.nextRunLine(now.addingTimeInterval(-300), now: now) == "Due now")
        #expect(NowStripView.nextRunLine(now.addingTimeInterval(20), now: now) == "Due now")
    }

    @Test("Settings calls the vault the Scout folder, as the glossary does")
    func scoutFolderWording() {
        #expect(GlossaryTerm.scoutFolder.term == "Scout folder")
        let help = EngineSettingsModel(state: .notInstalled, doctor: nil, lastError: nil, bundledVersion: nil).vaultHelp
        #expect(!help.lowercased().contains("vault"), "\(help)")
    }
}
