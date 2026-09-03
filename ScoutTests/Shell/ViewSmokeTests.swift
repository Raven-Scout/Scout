import AppKit
import SwiftUI
import Testing
@testable import Scout

/// Renders real SwiftUI views against a fully-wired but inert `AppState`.
///
/// These are smoke tests, not snapshot tests: they assert that a view's `body`
/// — and every computed sub-view, formatter, and branch it reaches — evaluates
/// and lays out without trapping. That catches the failure mode SwiftUI is
/// most prone to (a force-unwrap, an out-of-range index, or a precondition in
/// a view builder) which the compiler cannot, and which previously could only
/// be found by launching the app and clicking into the tab.
@MainActor
enum ViewHost {

    /// Mount `view` in a hosting view and force a full layout + display pass,
    /// so lazy containers materialise their children.
    ///
    /// Deliberately does *not* pump the run loop: these suites are
    /// `.serialized`, and re-entering the main run loop from inside a test
    /// crashes the host. Layout alone is enough to evaluate `body` and every
    /// computed sub-view it reaches.
    static func render<V: View>(
        _ view: V,
        size: CGSize = CGSize(width: 1280, height: 860)
    ) {
        let host = NSHostingView(rootView: view)
        host.frame = CGRect(origin: .zero, size: size)

        // An off-screen window gives lazy containers (ScrollView, LazyVStack,
        // List) a real display context, so they materialise their children
        // instead of laying out an empty viewport.
        let window = NSWindow(
            contentRect: host.frame,
            styleMask: [.titled, .resizable],
            backing: .buffered,
            defer: true)
        window.contentView = host

        host.layoutSubtreeIfNeeded()
        _ = host.fittingSize
        host.displayIfNeeded()

        window.contentView = nil
    }
}

/// A vault laid out the way Scout expects, so views render populated rather
/// than falling straight into their empty states.
@MainActor
struct SmokeVault {
    let root: URL
    let state: AppState

    init() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("smoke-vault-\(UUID().uuidString)")
            .resolvingSymlinksInPath()
        self.root = root

        let fm = FileManager.default
        for sub in ["action-items", "dreaming-proposals", "knowledge-base/people",
                    "knowledge-base/projects", "wishlist", "research-queue",
                    ".scout-logs", ".scout-state", ".scout-cache"] {
            try fm.createDirectory(at: root.appendingPathComponent(sub),
                                   withIntermediateDirectories: true)
        }

        try Self.write(Self.actionItems, to: root
            .appendingPathComponent("action-items/2026-06-15.md"))
        try Self.write(Self.proposal, to: root
            .appendingPathComponent("dreaming-proposals/2026-06-15-tighten-cadence.md"))
        try Self.write(Self.wishlistItem, to: root
            .appendingPathComponent("wishlist/2026-06-15-batch-the-digest.md"))
        try Self.write(Self.researchItem, to: root
            .appendingPathComponent("research-queue/2026-06-14-tracing-costs.md"))
        try Self.write("---\ntype: person\n---\n# Alex\nWorks on [[the-demo]].\n",
                       to: root.appendingPathComponent("knowledge-base/people/alex.md"))
        try Self.write("---\ntype: project\n---\n# The demo\nOwned by [[alex]].\n",
                       to: root.appendingPathComponent("knowledge-base/projects/the-demo.md"))
        try Self.write(Self.sessionLog, to: root
            .appendingPathComponent(".scout-logs/scout-2026-06-15_08-03.log"))

        self.state = AppState(configuration: .testing(scoutDirectory: root))
    }

    /// Load the document services synchronously so views render with content.
    func loadDocuments() async {
        state.proposalsDocumentService.load()
        state.wishlistDocumentService.load()
        state.researchDocumentService.load()
        state.knowledgeBaseService.load()
        await state.knowledgeBaseService.reparseAndWait()
    }

    func tearDown() {
        try? FileManager.default.removeItem(at: root)
    }

    private static func write(_ text: String, to url: URL) throws {
        try text.write(to: url, atomically: true, encoding: .utf8)
    }

    // MARK: - Fixture text (anonymized per CLAUDE.md)

    private static let actionItems = """
    # Monday, June 15 2026

    A short preamble paragraph before the first section.

    ## 🔴 Urgent
    - [ ] [#MIRO] Reply to Priya — she needs the RFC by Friday
    - [x] [#RSM] Land PROJ-1234 — merged this morning

    ## 🟡 To do
    - [ ] [#AI3026] Review the tracing job — `--shortstat` output looks off
    - [ ] Check [[projects/the-demo]] — **blocked** on Sam

    ## 📅 Meetings
    | Time | Who | What |
    | --- | --- | --- |
    | 09:00 | Alex | Standup |
    | 14:00 | Priya | RFC review |

    ## 📋 Digest
    - A digest line with a [link](https://github.com/example-org/app/pull/42).
    """

    private static let proposal = """
    ---
    date: 2026-06-15
    title: Tighten the consolidation cadence
    status: Pending (auto-apply after 2026-06-20)
    target: SKILL.md
    ---

    # 2026-06-15 — Tighten the consolidation cadence
    **Trigger:** the demo kept timing out.

    ```
    cooldown_minutes: 30
    ```
    """

    private static let wishlistItem = """
    ---
    title: Batch the digest
    status: open
    priority: high
    date: 2026-06-15
    ---

    # Batch the digest
    Group digest lines by source.
    """

    private static let researchItem = """
    ---
    title: Tracing costs
    status: in-progress
    priority: medium
    date: 2026-06-14
    area: observability
    ---

    # Tracing costs
    Compare the two vendors.
    """

    private static let sessionLog = """
    === SCOUT run starting at Mon Jun 15 08:03:01 EDT 2026 ===
    Reading action items…
    === SCOUT run finished at Mon Jun 15 08:09:11 EDT 2026 (exit 0) ===
    """
}

@MainActor
@Suite("View smoke — shell", .serialized)
struct ShellViewSmokeTests {

    @Test("the main window renders with a fully wired state")
    func mainWindowRenders() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        await vault.loadDocuments()
        ViewHost.render(
            MainWindowView()
                .environmentObject(vault.state)
                .environmentObject(vault.state.proposalsDocumentService))
    }

    @Test("every sidebar destination has a status label")
    func sidebarItemsHaveStatusLabels() {
        let labels = SidebarItem.allCases.map(\.statusLabel)
        #expect(labels.count == 8)
        #expect(Set(labels).count == labels.count)
        #expect(labels.allSatisfy { !$0.isEmpty })
    }

    @Test("the sidebar renders for every destination")
    func sidebarRendersEverySelection() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        for item in SidebarItem.allCases {
            var selection = item
            let binding = Binding(get: { selection }, set: { selection = $0 })
            ViewHost.render(
                SidebarView(selection: binding).environmentObject(vault.state),
                size: CGSize(width: 240, height: 700))
        }
    }

    @Test("the status bar renders for each menu-bar status")
    func statusBarRendersEveryStatus() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        for status in [AppState.MenuBarStatus.idle, .running, .lastFailed, .budgetSkipped] {
            vault.state.menuBarStatus = status
            ViewHost.render(
                StatusBarView(viewLabel: "Control Center").environmentObject(vault.state),
                size: CGSize(width: 1000, height: 26))
        }
    }

    @Test("the menu bar extra renders")
    func menuBarExtraRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(
            MenuBarExtraContent().environmentObject(vault.state),
            size: CGSize(width: 320, height: 400))
    }

    @Test("the menu bar icon renders for every status")
    func menuBarIconRendersEveryStatus() {
        for status in [AppState.MenuBarStatus.idle, .running, .lastFailed, .budgetSkipped] {
            ViewHost.render(MenuBarIcon(status: status), size: CGSize(width: 24, height: 24))
        }
    }

    @Test("the settings pane renders")
    func settingsRenders() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(
            SettingsView().environmentObject(vault.state),
            size: CGSize(width: 700, height: 620))
    }
}
