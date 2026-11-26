import AppKit
import SwiftUI
import Testing
@testable import Scout

/// Hand check of #767, third pass: on a page just navigated to, with the
/// sidebar holding the keys, a click on a row or card picked it but gave the
/// list no keys, so ↓ did nothing, on every list (Schedules Table and Cards,
/// Action Items, the KB tree). Only after Tab had once reached the list did
/// clicks work. The list's focus sink was a SwiftUI `.focusable()` view,
/// which ignores `focus = true` from code until it has been focused once;
/// `DSFocusSink` is an AppKit view, which takes it every time.
///
/// Each test shows its list only after the window is up, then gives the
/// sidebar the keys, as arriving at the page does. A list in the window from
/// the start can be focused from code, so it never showed the bug.
///
/// What these tests pin is that the list's own AppKit view is first
/// responder after the click, which is what the hand check's ↓ needs. They
/// can't show ↓ itself failing: in a test window, even a split view's, the
/// old sink's `@FocusState` turned true and ↓ moved (checked against the old
/// sink), while in the app the same click left ↓ inert. A standalone
/// `WindowGroup` app with a split view did reproduce it, and the AppKit sink
/// fixed it there.
@MainActor
private final class FreshLog {
    var showList: () -> Void = {}
    var focusField: () -> Void = {}
    var fieldFocused = false
    var listFocused = false
    var selection: String?
    var cursor: UUID?
    /// A card's tap as `ActionItemsView` wires it, called directly.
    var clickCard: (UUID) -> Void = { _ in }
}

/// The app's shell: a `NavigationSplitView` whose sidebar `List` picks the
/// page, and whose detail shows `content` only on `showList`, as arriving at
/// a page does. The bug needs the split view: in a plain `NSHostingView`
/// the old sink took `focus = true` from code even on a fresh page.
private struct FreshPage<Content: View>: View {
    static var sidebarWidth: CGFloat { 200 }

    let log: FreshLog
    @ViewBuilder let content: () -> Content
    @State private var page: String? = "Elsewhere"

    var body: some View {
        NavigationSplitView {
            List(["Elsewhere", "Here"], id: \.self, selection: $page) { Text($0) }
                .navigationSplitViewColumnWidth(Self.sidebarWidth)
        } detail: {
            if page == "Here" { content() } else { Text("Elsewhere") }
        }
        .onAppear { log.showList = { page = "Here" } }
    }
}

/// The Action Items List as `ActionItemsView` wires it (see `CardsProbe`),
/// with a search field above it.
private struct FreshCards: View {
    static let tasks: [ActionTask] = (1...3).map { i in
        SmokeFixtures.task(subject: "Card \(i)", body: "Walk Sam through the rollout checklist before Thursday.")
    }

    let log: FreshLog
    @State private var cursor: UUID?
    @State private var search = ""
    @State private var clickFocus = CardClickFocus()
    @FocusState private var fieldFocused: Bool
    @FocusState private var listFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            TextField("Search", text: $search).focused($fieldFocused).frame(height: 28)
            ScrollView {
                VStack(spacing: 0) {
                    ForEach(Self.tasks) { task in
                        TaskCardView(task: task, kind: .urgent, displayedDate: SmokeFixtures.day,
                                     scoutDirectory: URL(fileURLWithPath: "/tmp/scout"), onOp: { _, _ in })
                    }
                }
                .padding(16)
            }
            .environment(\.actionItemsKeyboard, ActionItemsKeyboardContext(
                cursorID: cursor,
                ringID: KeyboardListNavigation.ringed(cursor, in: Self.tasks.map(\.id)),
                focusCard: StableAction { id in click(id) }))
            .environment(\.cardClickFocus, clickFocus)
            .dsFocusContainer($listFocused)
            .keyboardListNavigation(when: listFocused, ids: { Self.tasks.map(\.id) }, selection: $cursor)
        }
        .onAppear {
            clickFocus.start()
            log.focusField = { fieldFocused = true }
            log.clickCard = { click($0) }
        }
        .onDisappear { clickFocus.stop() }
        .onChange(of: cursor) { _, id in log.cursor = id }
        .onChange(of: fieldFocused) { _, focused in log.fieldFocused = focused }
        .onChange(of: listFocused) { _, focused in log.listFocused = focused }
    }

    private func click(_ id: UUID) {
        clickFocus.clicked(id, setCursor: { cursor = $0 }, focusList: { listFocused = true })
    }
}

/// The KB tree with three notes, its selection reported.
private struct FreshTree: View {
    static let nodes: [KBNode] = ["alex.md", "priya.md", "sam.md"].map { name in
        KBNode(kind: .file, url: URL(fileURLWithPath: "/vault/people/\(name)"),
               relativePath: "people/\(name)", name: name, ext: "md", children: [])
    }

    let log: FreshLog
    @State private var selected: String?
    @State private var expanded: Set<String> = []

    var body: some View {
        KBTreeView(nodes: Self.nodes, selectedPath: $selected, expanded: $expanded)
            .onChange(of: selected) { _, path in log.selection = path }
    }
}

extension FocusContainerWindowTests {
    private func openFresh<V: View>(_ view: V, width: CGFloat, height: CGFloat = 520) async throws -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = NSHostingView(rootView: view.frame(width: width, height: height))
        window.makeKeyAndOrderFront(nil)
        try await Task.sleep(for: .milliseconds(400))
        return window
    }

    private func wait(_ ms: Int = 200) async throws { try await Task.sleep(for: .milliseconds(ms)) }

    private func holder(_ window: NSWindow) -> String {
        window.firstResponder.map { String(describing: type(of: $0)) } ?? "nothing"
    }

    /// The split view's sidebar, an outline view.
    private func sidebar(in view: NSView) -> NSOutlineView? {
        if let outline = view as? NSOutlineView { return outline }
        return view.subviews.lazy.compactMap { sidebar(in: $0) }.first
    }

    /// Gives the sidebar the keys (as AppKit: the app's sidebar has no focus
    /// state, and a synthetic click doesn't focus it), then shows the list:
    /// a freshly shown page.
    private func arriveFromTheSidebar(_ log: FreshLog, in window: NSWindow) async throws {
        let outline = try #require(window.contentView.flatMap { sidebar(in: $0) }, "no sidebar outline")
        log.showList()
        try await wait(400)
        _ = window.makeFirstResponder(outline)
        try await wait(300)
        try #require(window.firstResponder === outline, "the sidebar didn't take the keys")
    }

    /// Clicks down the list's first column until a click picks an item.
    private func clickTheFirstItem(_ log: FreshLog, in window: NSWindow, picked: () -> Bool) async throws {
        let x = FreshPage<EmptyView>.sidebarWidth + 60
        let landed = try await WindowEvents.clickDown(x: x, fromTop: 30, in: window, until: picked)
        try #require(landed != nil, "no click picked an item")
        try await wait(400)
    }

    @Test("a freshly shown Schedules list, the sidebar holding the keys: a click on an item gives the list the keys",
          arguments: [false, true])
    func freshScheduleClickTakesTheKeys(cards: Bool) async throws {
        try await KeyWindowGate.run {
            let log = FreshLog()
            let selection = Binding<String?>(get: { log.selection }, set: { log.selection = $0 })
            let page = FreshPage(log: log) {
                SchedulesMasterList(cards: cards, slots: SmokeFixtures.slots, newDraftSlot: nil,
                                    selectedSlotKey: selection)
            }
            let window = try await openFresh(page, width: FreshPage<EmptyView>.sidebarWidth + 1000)
            defer { window.close() }
            try await arriveFromTheSidebar(log, in: window)
            try await clickTheFirstItem(log, in: window) { log.selection != nil }
            #expect(window.firstResponder is DSFocusSinkView, "the keys are on \(holder(window)), not the list")
            let clicked = log.selection
            WindowEvents.press(WindowEvents.down, in: window)
            try await wait()
            #expect(log.selection != clicked, "↓ after the click did nothing; the keys are on \(holder(window))")
        }
    }

    @Test("a freshly shown Action Items list, the sidebar holding the keys: a card's tap gives the list the keys")
    func freshCardTapTakesTheKeys() async throws {
        // A card's tap through the real CardClickFocus, called directly: the
        // harness's clicks don't reach a card (see cardClickFocusesTheList).
        try await KeyWindowGate.run {
            let log = FreshLog()
            let window = try await openFresh(FreshPage(log: log) { FreshCards(log: log) },
                                             width: FreshPage<EmptyView>.sidebarWidth + 560, height: 640)
            defer { window.close() }
            try await arriveFromTheSidebar(log, in: window)
            log.clickCard(FreshCards.tasks[0].id)
            try await wait(300)
            #expect(log.cursor == FreshCards.tasks[0].id, "the tap didn't make the card current")
            #expect(log.listFocused && window.firstResponder is DSFocusSinkView,
                    "the keys are on \(holder(window)), not the list")
            WindowEvents.press(WindowEvents.down, in: window)
            try await wait()
            #expect(log.cursor == FreshCards.tasks[1].id, "↓ after the tap did nothing")
        }
    }

    @Test("a freshly shown KB tree, the sidebar holding the keys: a click on a row gives the tree the keys")
    func freshTreeClickTakesTheKeys() async throws {
        try await KeyWindowGate.run {
            let log = FreshLog()
            let window = try await openFresh(FreshPage(log: log) { FreshTree(log: log) },
                                             width: FreshPage<EmptyView>.sidebarWidth + 300)
            defer { window.close() }
            try await arriveFromTheSidebar(log, in: window)
            try await clickTheFirstItem(log, in: window) { log.selection != nil }
            #expect(window.firstResponder is DSFocusSinkView, "the keys are on \(holder(window)), not the tree")
            let clicked = log.selection
            WindowEvents.press(WindowEvents.down, in: window)
            try await wait()
            #expect(log.selection != clicked, "↓ after the click did nothing; the keys are on \(holder(window))")
        }
    }

    @Test("Tab from the search field reaches the Action Items list's own stop, with no stop between")
    func tabReachesTheCardsList() async throws {
        // Hand check of #767: Tab and shift-Tab never reached the list's stop;
        // Tab went from the field onto the first card's controls.
        try await KeyWindowGate.run {
            let log = FreshLog()
            let window = try await openFresh(FreshPage(log: log) { FreshCards(log: log) },
                                             width: FreshPage<EmptyView>.sidebarWidth + 560, height: 640)
            defer { window.close() }
            log.showList()
            try await wait(400)
            log.focusField()
            try await wait(300)
            try #require(log.fieldFocused, "the field didn't take the keys")
            var stops = 0
            while !log.listFocused, stops < 20 {
                WindowEvents.press(WindowEvents.tab, in: window)
                try await wait(120)
                stops += 1
            }
            #expect(log.listFocused, "Tab never reached the list; it stopped on \(holder(window))")
            // With Keyboard navigation off, only the list takes Tab after the
            // field; with it on, nothing in the cards comes before the list.
            #expect(stops == 1, "Tab stopped \(stops - 1) time(s) between the field and the list")
            WindowEvents.press(WindowEvents.down, in: window)
            try await wait()
            #expect(log.cursor != nil, "↓ on the list's stop did nothing")
            shiftTab(in: window)
            try await wait(200)
            // In this split-view probe, shift-Tab from the list skips the
            // field for the sidebar, with the old SwiftUI sink as with this
            // one. Not yet seen in the app; the hand check looks.
            withKnownIssue("shift-Tab from the list goes to the sidebar, not the field", isIntermittent: true) {
                #expect(log.fieldFocused, "shift-Tab from the list went to \(holder(window)), not the field")
            }
        }
    }

    private func shiftTab(in window: NSWindow) {
        for type in [NSEvent.EventType.keyDown, .keyUp] {
            guard let event = NSEvent.keyEvent(
                with: type, location: .zero, modifierFlags: [.shift], timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: window.windowNumber, context: nil,
                characters: "\u{19}", charactersIgnoringModifiers: "\t",
                isARepeat: false, keyCode: 48
            ) else { continue }
            window.sendEvent(event)
        }
    }
}
