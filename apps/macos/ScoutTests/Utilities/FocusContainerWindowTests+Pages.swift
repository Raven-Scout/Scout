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
          arguments: [false, true])
    func tabCycle(cards: Bool) async throws {
        guard NSApplication.shared.isFullKeyboardAccessEnabled else { return }
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

    @Test("Keyboard navigation: a card's ref link is one Tab stop, its header chip, not a second one under the description")
    func aRefLinkIsOneTabStop() async throws {
        guard NSApplication.shared.isFullKeyboardAccessEnabled else { return }
        try await KeyWindowGate.run {
            let body = "Check → [[projects/the-demo]] before Friday."
            let plain = try await cardTabStops(SmokeFixtures.task(subject: "Check the demo", body: body))
            let linked = try await cardTabStops(SmokeFixtures.task(
                subject: "Check the demo", body: body, deepLinks: [.entity(path: "projects/the-demo", label: nil)]))
            #expect(linked == plain + 1, "a ref link added \(linked - plain) Tab stops")
        }
    }

    @Test("a click on a card's body while a field elsewhere has the keys gives the list the keys: ↓ then moves")
    func cardClickFocusesTheList() async throws {
        try await KeyWindowGate.run {
            let log = CardsLog()
            let window = try await open(CardsProbe(log: log), width: 560, height: 640)
            defer { window.close() }
            // The keys start on the field, as when arriving from the search.
            log.focusField()
            try await settle(300)
            try #require(log.fieldFocused && log.cursor == nil, "the field didn't take the keys")
            // The first card's middle: its description, clear of the header
            // (whose title opens and closes the card) and the buttons below.
            // Measured, not scanned: a scan click could open a menu, whose
            // tracking loop never returns in a test.
            let card = try #require(log.firstCard, "the first card never laid out")
            let height = try #require(window.contentView?.bounds.height)
            try #require(card.maxY < height, "the first card runs past the window")
            WindowEvents.click(atTop: card.midY, x: card.midX, in: window)
            try await settle()
            #expect(log.cursor == CardsProbe.tasks[0].id, "the click didn't make the card current")
            #expect(!log.fieldFocused, "the field kept the keys")
            WindowEvents.press(WindowEvents.down, in: window)
            try await settle()
            #expect(log.cursor == CardsProbe.tasks[1].id, "↓ after a click on the card did nothing")
        }
    }

    @Test("a click on a card takes the list's focus unless it left the keys with text")
    func clickTakesListFocusRule() {
        #expect(ActionItemsKeyboardContext.clickTakesListFocus(firstResponder: nil))
        #expect(ActionItemsKeyboardContext.clickTakesListFocus(firstResponder: NSView()))
        // A comment editor the click went into keeps the keys.
        let editor = NSTextView()
        #expect(!ActionItemsKeyboardContext.clickTakesListFocus(firstResponder: editor))
        // The search field's shared editor gives way: the click was on the list.
        let fieldEditor = NSTextView()
        fieldEditor.isFieldEditor = true
        #expect(ActionItemsKeyboardContext.clickTakesListFocus(firstResponder: fieldEditor))
        // Text the click just selected stays selected for ⌘C.
        let selectable = NSTextView()
        selectable.isEditable = false
        selectable.string = "Reply to Priya"
        selectable.setSelectedRange(NSRange(location: 0, length: 5))
        #expect(!ActionItemsKeyboardContext.clickTakesListFocus(firstResponder: selectable))
        selectable.setSelectedRange(NSRange(location: 2, length: 0))
        #expect(ActionItemsKeyboardContext.clickTakesListFocus(firstResponder: selectable))
    }
}

@MainActor
private final class CardsLog {
    var cursor: UUID?
    var fieldFocused = false
    /// The first card's frame, top-down from the window content's top-left.
    var firstCard: CGRect?
    var focusField: () -> Void = {}
}

/// The Action Items List as `ActionItemsView` wires it: real cards, the
/// list's focus sink and arrow keys, and its `focusCard` (a click on a card),
/// with a search field above that has the keys to start with.
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
                            .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { frame in
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
                    cursor = id
                    if ActionItemsKeyboardContext.clickTakesListFocus(firstResponder: NSApp.keyWindow?.firstResponder) {
                        listFocused = true
                    }
                }))
            .dsFocusContainer($listFocused)
            .keyboardListNavigation(when: listFocused, ids: { Self.tasks.map(\.id) }, selection: $cursor)
        }
        .onAppear { log.focusField = { fieldFocused = true } }
        .onChange(of: cursor) { _, id in log.cursor = id }
        .onChange(of: fieldFocused) { _, focused in log.fieldFocused = focused }
    }
}

