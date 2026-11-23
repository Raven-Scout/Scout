import AppKit
import SwiftUI
import Testing
@testable import Scout

/// What a probe page reports from outside the list under test.
@MainActor
private final class PageLog {
    var selection: String?
    var chipFocused = false
    var sidebarFocused = false
    /// Puts the keys on the sidebar, as when arriving from it.
    var focusSidebar: () -> Void = {}
    /// Clears the pick and lets the next one open the detail pane.
    var startOver: () -> Void = {}
}

/// The Schedules list as the page lays it out: a chip above, optionally a
/// sidebar list to its left (`sidebar`), and a detail pane beside it once a
/// schedule is picked (`paneWidth`), which narrows the list.
private struct SchedulesProbe: View {
    static let sidebarWidth: CGFloat = 140

    let log: PageLog
    var cards = false
    var sidebar = false
    var paneWidth: CGFloat = 0
    @State private var selection: String?
    @State private var paneOpens = true
    @State private var page: String? = "Schedules"
    @FocusState private var chipFocused: Bool
    @FocusState private var sidebarFocused: Bool

    var body: some View {
        HStack(spacing: 0) {
            if sidebar {
                List(["Action Items", "Schedules"], id: \.self, selection: $page) { Text($0) }
                    .focused($sidebarFocused)
                    .frame(width: Self.sidebarWidth)
            }
            page_
        }
        .onAppear {
            log.focusSidebar = { sidebarFocused = true }
            log.startOver = { selection = nil; paneOpens = true }
        }
        .onChange(of: selection) { _, key in log.selection = key }
        .onChange(of: chipFocused) { _, focused in log.chipFocused = focused }
        .onChange(of: sidebarFocused) { _, focused in log.sidebarFocused = focused }
    }

    private var page_: some View {
        VStack(spacing: 0) {
            Button("Chip") {}.buttonStyle(.plainHit).focused($chipFocused).frame(height: 24)
            HStack(spacing: 0) {
                Group {
                    if cards {
                        SchedulesMasterCards(slots: SmokeFixtures.slots, newDraftSlot: nil, selectedSlotKey: $selection)
                    } else {
                        SchedulesMasterTable(slots: SmokeFixtures.slots, newDraftSlot: nil, selectedSlotKey: $selection)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if paneWidth > 0, paneOpens, selection != nil {
                    Color.gray.frame(width: paneWidth)
                }
            }
        }
        .onAppear { if paneWidth > 0 { paneOpens = false } }
    }
}

/// UI audit T4 hand check, second pass: a click on a Schedules row or card
/// gives the list the keys, the Table's Tab stop comes before its rows and is
/// their only one, and a click that stacks the Table into cards keeps the
/// focus on the list. Real clicks and keys into one in-process window.
extension FocusContainerWindowTests {
    fileprivate func open<V: View>(_ view: V, width: CGFloat, height: CGFloat = 520) async throws -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = NSHostingView(rootView: view.frame(width: width, height: height))
        window.makeKeyAndOrderFront(nil)
        try await Task.sleep(for: .milliseconds(400))
        return window
    }

    fileprivate func settle(_ ms: Int = 200) async throws { try await Task.sleep(for: .milliseconds(ms)) }

    /// The schedule after `key` in the order the arrow keys walk.
    private func after(_ key: String?) -> String? {
        let keys = SmokeFixtures.slots.map(\.key)
        guard let key, let i = keys.firstIndex(of: key), i + 1 < keys.count else { return nil }
        return keys[i + 1]
    }

    /// What has the keys, for a failure message.
    private func responder(_ window: NSWindow) -> String {
        window.firstResponder.map { String(describing: type(of: $0)) } ?? "nothing"
    }

    /// Finds the first item down the list's first column (the first click
    /// that picks a schedule; a probe with a detail pane keeps it shut for
    /// this one), then starts over with the keys on the sidebar and nothing
    /// picked, and clicks that item again: the click under test.
    private func clickFirstItem(_ log: PageLog, in window: NSWindow) async throws -> String {
        let x = SchedulesProbe.sidebarWidth + 120
        let landed = try await WindowEvents.clickDown(x: x, fromTop: 40, in: window) { log.selection != nil }
        let top = try #require(landed, "no click picked a schedule")
        log.startOver()
        log.focusSidebar()
        try await settle(300)
        try #require(log.sidebarFocused && log.selection == nil, "the sidebar didn't take the keys")
        WindowEvents.click(atTop: top, x: x, in: window)
        try await settle(400)
        return try #require(log.selection, "the click didn't pick a schedule")
    }

    // MARK: Click → focus (runs everywhere)

    @Test("a click on a Schedules row or card gives the list the keys: ↓ then moves from the clicked item",
          arguments: [false, true])
    func clickFocusesTheList(cards: Bool) async throws {
        try await KeyWindowGate.run {
            let log = PageLog()
            let window = try await open(SchedulesProbe(log: log, cards: cards, sidebar: true),
                                        width: SchedulesProbe.sidebarWidth + 1000)
            defer { window.close() }
            let clicked = try await clickFirstItem(log, in: window)
            WindowEvents.press(WindowEvents.down, in: window)
            try await settle()
            #expect(log.selection == after(clicked),
                    "↓ after a click on \(clicked) did nothing; the keys are on \(responder(window))")
        }
    }

    @Test("a click that opens the detail pane and stacks the table keeps the keys on the list, at the clicked row")
    func stackingKeepsTheFocus() async throws {
        try await KeyWindowGate.run {
            let log = PageLog()
            let columns = SchedulesColumns()
            // Wide enough for the table's columns; narrower than that once the pane opens.
            let list = columns.minTotal(.dots) + 40
            try #require(columns.mode(forAvailable: list) != .stacked)
            try #require(columns.mode(forAvailable: list - 340) == .stacked)
            let window = try await open(SchedulesProbe(log: log, sidebar: true, paneWidth: 340),
                                        width: SchedulesProbe.sidebarWidth + list)
            defer { window.close() }
            let clicked = try await clickFirstItem(log, in: window)
            #expect(!log.chipFocused, "the focus left the list for the control above it")
            #expect(!log.sidebarFocused, "the sidebar kept the keys")
            WindowEvents.press(WindowEvents.down, in: window)
            try await settle()
            #expect(log.selection == after(clicked),
                    "↓ after the stacking click did nothing; the keys are on \(responder(window))")
        }
    }

    // MARK: Tab order

    /// Tabs until the list's own stop, which ↓ shows by moving the selection.
    /// Returns how many stops Tab passed on the way that weren't the chip:
    /// items that are Tab stops of their own. Nil when it never got there.
    private func strayStopsBeforeTheList(_ log: PageLog, in window: NSWindow) async throws -> Int? {
        var stray = 0
        for _ in 0..<12 {
            WindowEvents.press(WindowEvents.tab, in: window)
            try await settle(150)
            if log.chipFocused { continue }
            let before = log.selection
            WindowEvents.press(WindowEvents.down, in: window)
            try await settle(150)
            if log.selection != before { return stray }
            stray += 1
        }
        return nil
    }

    @Test("Tab reaches the Schedules list's own stop with no item stop before it", arguments: [false, true])
    func tabReachesTheListFirst(cards: Bool) async throws {
        // Without Keyboard navigation only the list's own stop takes Tab, so
        // this holds by itself there; with it the chip and (before T4's second
        // pass) every row took Tab too, the first row before the list.
        try await KeyWindowGate.run {
            let log = PageLog()
            let window = try await open(SchedulesProbe(log: log, cards: cards), width: 1000)
            defer { window.close() }
            let stray = try await strayStopsBeforeTheList(log, in: window)
            #expect(stray == 0, "Tab stopped on \(stray.map(String.init) ?? "every") item(s) before the list")
        }
    }

    @Test("Keyboard navigation: Tab goes chip → the list's stop → back to the chip, never onto an item",
          .enabled(if: WindowEvents.keyboardNavigationOn, "needs Keyboard navigation on"),
          arguments: [false, true])
    func tabCycle(cards: Bool) async throws {
        try await KeyWindowGate.run {
            let log = PageLog()
            let window = try await open(SchedulesProbe(log: log, cards: cards), width: 1000)
            defer { window.close() }
            for _ in 0..<4 where !log.chipFocused {
                WindowEvents.press(WindowEvents.tab, in: window)
                try await settle(150)
            }
            try #require(log.chipFocused, "Tab never reached the chip")
            WindowEvents.press(WindowEvents.tab, in: window)
            try await settle(150)
            #expect(!log.chipFocused)
            WindowEvents.press(WindowEvents.down, in: window)
            try await settle(150)
            #expect(log.selection != nil, "the stop after the chip isn't the list's")
            // The list's stop is its items' only one: the next Tab wraps to the chip.
            WindowEvents.press(WindowEvents.tab, in: window)
            try await settle(150)
            #expect(log.chipFocused, "Tab stopped on an item after the list's own stop")
        }
    }

    // MARK: Action Items

    /// Distinct Tab stops in one card, once round.
    private func cardTabStops(_ task: ActionTask) async throws -> Int {
        let view = TaskCardView(task: task, kind: .urgent, displayedDate: SmokeFixtures.day,
                                scoutDirectory: URL(fileURLWithPath: "/tmp/scout"), onOp: { _, _ in })
            .environment(\.openKBNote, StableAction { _ in })
            .padding(20)
        let window = try await open(view, width: 560, height: 420)
        defer { window.close() }
        var seen: Set<ObjectIdentifier> = []
        for _ in 0..<30 {
            WindowEvents.press(WindowEvents.tab, in: window)
            try await settle(100)
            if let responder = window.firstResponder { seen.insert(ObjectIdentifier(responder)) }
        }
        return seen.count
    }

    @Test("Keyboard navigation: a card's ref link is one Tab stop, its header chip, not a second one under the description",
          .enabled(if: WindowEvents.keyboardNavigationOn, "needs Keyboard navigation on"))
    func aRefLinkIsOneTabStop() async throws {
        try await KeyWindowGate.run {
            let body = "Check → [[projects/the-demo]] before Friday."
            let plain = try await cardTabStops(SmokeFixtures.task(subject: "Check the demo", body: body))
            let linked = try await cardTabStops(SmokeFixtures.task(
                subject: "Check the demo", body: body, deepLinks: [.entity(path: "projects/the-demo", label: nil)]))
            #expect(linked == plain + 1, "a ref link added \(linked - plain) Tab stops")
        }
    }

    /// Clicks the first card's header in its blank top padding: on the card,
    /// above the title (which opens and closes the card), clear of every
    /// control. Not the description: it is selectable text, which takes the
    /// click for a selection, so the card's tap never sees it. Not a scan
    /// either: a click that opens a menu runs its tracking loop on the main
    /// thread and never returns in a test.
    private func clickFirstCard(_ log: CardsLog, in window: NSWindow) async throws {
        let card = try #require(log.firstCard, "the first card never laid out")
        WindowEvents.click(atTop: card.minY + 5, x: card.midX, in: window)
        try await settle()
    }

    @Test("a click on a card while a field elsewhere has the keys gives the list the keys: ↓ then moves")
    func cardClickFocusesTheList() async throws {
        try await KeyWindowGate.run {
            let log = CardsLog()
            let window = try await open(CardsProbe(log: log), width: 560, height: 680)
            defer { window.close() }
            // The keys start on the field, as when arriving from the search.
            log.focusField()
            try await settle(300)
            try #require(log.fieldFocused && log.cursor == nil, "the field didn't take the keys")
            try await clickFirstCard(log, in: window)
            #expect(log.cursor == CardsProbe.tasks[0].id, "the click didn't make the card current")
            #expect(!log.fieldFocused, "the field kept the keys")
            WindowEvents.press(WindowEvents.down, in: window)
            try await settle()
            #expect(log.cursor == CardsProbe.tasks[1].id, "↓ after a click on the card did nothing")
        }
    }

    @Test("a click on a card while another card's comment editor has the keys gives the list the keys")
    func cardClickTakesTheKeysFromAnotherEditor() async throws {
        // Review of #767: the click rule read only what held the keys once the
        // click was done, so a comment being written elsewhere kept them, the
        // card turned current with no ring, and ↓ moved the caret.
        try await KeyWindowGate.run {
            let log = CardsLog()
            let window = try await open(CardsProbe(log: log), width: 560, height: 680)
            defer { window.close() }
            log.focusEditor()
            try await settle(300)
            try #require(log.editorFocused, "the editor didn't take the keys")
            WindowEvents.type("draft", in: window)
            try await settle()
            try #require(log.editorText == "draft", "typing didn't reach the editor: \(log.editorText)")
            try await clickFirstCard(log, in: window)
            #expect(log.cursor == CardsProbe.tasks[0].id, "the click didn't make the card current")
            #expect(!log.editorFocused, "the editor kept the keys")
            WindowEvents.press(WindowEvents.down, in: window)
            try await settle()
            #expect(log.cursor == CardsProbe.tasks[1].id, "↓ after the click did nothing")
            #expect(log.editorText == "draft", "the click or ↓ changed the draft")
        }
    }

    @Test("Add comment in a card: the editor it opens has the keys, so typing at once goes into it")
    func addCommentKeepsTheKeys() async throws {
        // Review of #767: the card's tap gave the list the keys while Add
        // comment focused its editor a turn later.
        try await KeyWindowGate.run {
            let log = ComposerLog()
            let window = try await open(ComposerProbe(log: log), width: 420, height: 260)
            defer { window.close() }
            let button = try #require(log.button, "Add comment never laid out")
            WindowEvents.click(atTop: button.midY, x: button.midX, in: window)
            try await settle(400)
            WindowEvents.type("hi", in: window)
            try await settle()
            let editor = window.firstResponder as? NSTextView
            #expect(editor?.isEditable == true && editor?.isFieldEditor == false,
                    "the keys aren't in a comment editor: \(String(describing: window.firstResponder))")
            #expect(editor?.string == "hi",
                    "typing didn't reach the new editor")
            #expect(!log.listFocused, "the list took the keys from the editor")
        }
    }
}

/// A probe's own space, top-down from its top-left: the window content's.
/// (SwiftUI's global space starts above it, under the title bar.)
private let probeSpace = "focus-probe"

@MainActor
private final class CardsLog {
    var cursor: UUID?
    var fieldFocused = false
    var editorFocused = false
    var editorText = ""
    /// The first card's frame, in `probeSpace`.
    var firstCard: CGRect?
    var focusField: () -> Void = {}
    var focusEditor: () -> Void = {}
}

/// The Action Items List as `ActionItemsView` wires it: real cards, the
/// list's focus sink and arrow keys, and its `focusCard` through the real
/// `CardClickFocus` (the decision lives there; this only wires it as
/// `ActionItemsView.keyboardContext` does, so a change to that wiring must
/// land here too). Above it, a search field and a comment editor standing
/// for another card's, either of which can hold the keys first.
private struct CardsProbe: View {
    static let tasks: [ActionTask] = (1...3).map { i in
        SmokeFixtures.task(
            subject: "Card \(i)",
            body: Array(repeating: "Walk Sam through the rollout checklist before Thursday.", count: 6)
                .joined(separator: " "))
    }

    let log: CardsLog
    @State private var cursor: UUID?
    @State private var search = ""
    @State private var note = ""
    @State private var clickFocus = CardClickFocus()
    @FocusState private var fieldFocused: Bool
    @FocusState private var editorFocused: Bool
    @FocusState private var listFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            TextField("Search", text: $search).focused($fieldFocused).frame(height: 28)
            TextEditor(text: $note).focused($editorFocused).frame(height: 40)
            ScrollView {
                VStack(spacing: 0) {
                    ForEach(Self.tasks) { task in
                        TaskCardView(task: task, kind: .urgent, displayedDate: SmokeFixtures.day,
                                     scoutDirectory: URL(fileURLWithPath: "/tmp/scout"), onOp: { _, _ in })
                            .onGeometryChange(for: CGRect.self) { $0.frame(in: .named(probeSpace)) } action: { frame in
                                if task.id == Self.tasks[0].id { log.firstCard = frame }
                            }
                    }
                }
                .padding(16)
            }
            .environment(\.actionItemsKeyboard, ActionItemsKeyboardContext(
                cursorID: cursor,
                ringID: KeyboardListNavigation.ringed(cursor, in: Self.tasks.map(\.id)),
                focusCard: StableAction { id in
                    clickFocus.clicked(id, setCursor: { cursor = $0 }, focusList: { listFocused = true })
                }))
            .environment(\.cardClickFocus, clickFocus)
            .dsFocusContainer($listFocused)
            .keyboardListNavigation(when: listFocused, ids: { Self.tasks.map(\.id) }, selection: $cursor)
        }
        .onAppear {
            clickFocus.start()
            log.focusField = { fieldFocused = true }
            log.focusEditor = { editorFocused = true }
        }
        .onDisappear { clickFocus.stop() }
        .coordinateSpace(.named(probeSpace))
        .onChange(of: cursor) { _, id in log.cursor = id }
        .onChange(of: fieldFocused) { _, focused in log.fieldFocused = focused }
        .onChange(of: editorFocused) { _, focused in log.editorFocused = focused }
        .onChange(of: note) { _, text in log.editorText = text }
    }
}

@MainActor
private final class ComposerLog {
    var button: CGRect?
    var listFocused = false
}

/// A card's Add comment inside a list wired as the Action Items List is: the
/// card's tap calls `CardClickFocus.clicked` alongside the button's action.
private struct ComposerProbe: View {
    static let task = SmokeFixtures.task(subject: "Card", body: "Walk Sam through the checklist.")

    let log: ComposerLog
    @State private var cursor: UUID?
    @State private var clickFocus = CardClickFocus()
    @FocusState private var listFocused: Bool

    var body: some View {
        let task = Self.task
        VStack(alignment: .leading) {
            CommentComposerView(task: task, displayedDate: SmokeFixtures.day, onSubmit: { _ in true })
                .onGeometryChange(for: CGRect.self) { $0.frame(in: .named(probeSpace)) } action: { frame in
                    if log.button == nil { log.button = frame }
                }
            Spacer()
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .contentShape(Rectangle())
        .simultaneousGesture(TapGesture().onEnded {
            clickFocus.clicked(task.id, setCursor: { cursor = $0 }, focusList: { listFocused = true })
        })
        .environment(\.cardClickFocus, clickFocus)
        .dsFocusContainer($listFocused)
        .keyboardListNavigation(when: listFocused, ids: { [task.id] }, selection: $cursor)
        .onAppear { clickFocus.start() }
        .onDisappear { clickFocus.stop() }
        .onChange(of: listFocused) { _, focused in log.listFocused = focused }
        .coordinateSpace(.named(probeSpace))
    }
}
