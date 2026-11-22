import AppKit
import SwiftUI
import Testing
@testable import Scout

/// What each item of a probe list sees, refreshed on every redraw.
@MainActor
private final class RingLog {
    /// Per item: the container's cursor ring is drawn on it.
    var cursorRing: [Int: Bool] = [:]
    /// Per item: `isFocused` as a button style inside it reads it. True was
    /// every DS control ringing at once (T4 hand re-check).
    var styleFocus: [Int: Bool] = [:]
    /// Per item: the button holds the keyboard focus itself.
    var ownFocus: [Int: Bool] = [:]
    var containerFocused = false

    var rings: Int {
        cursorRing.values.filter { $0 }.count + styleFocus.values.filter { $0 }.count
    }
}

/// Reads what a DS button style reads, from inside a button's label.
private struct StyleProbe: View {
    let index: Int
    let log: RingLog
    @Environment(\.isFocused) private var isFocused

    var body: some View {
        let _ = { log.styleFocus[index] = isFocused }()
        Text("Item \(index)")
    }
}

/// A focusable list as the app builds one: DS-styled buttons, the cursor
/// ring, and keys gated on the list's own focus.
private struct ProbeList: View {
    let log: RingLog
    let focusOnAppear: Bool
    @State private var current: Int? = 0
    @FocusState private var listFocused: Bool
    @FocusState private var item: Int?

    var body: some View {
        VStack(spacing: 8) {
            Button("Header") {}.buttonStyle(.dsSecondary)
            VStack(spacing: 8) {
                ForEach(0..<3) { i in
                    Button {
                        current = i
                        listFocused = true
                    } label: {
                        StyleProbe(index: i, log: log).frame(width: 200, height: 30)
                    }
                    .buttonStyle(i == 2 ? AnyButtonStyle(.dsSecondary) : AnyButtonStyle(.dsCard(cornerRadius: 10)))
                    .focused($item, equals: i)
                    .dsCursorRing(current == i)
                    .background(CursorProbe(index: i, isCurrent: current == i, log: log))
                }
            }
            .dsFocusContainer($listFocused)
            .keyboardListNavigation(when: listFocused, ids: { Array(0..<3) }, selection: $current)
        }
        .frame(width: 300, height: 300)
        .onChange(of: listFocused) { _, focused in log.containerFocused = focused }
        .onChange(of: item) { _, item in
            for i in 0..<3 { log.ownFocus[i] = item == i }
        }
        .onAppear { if focusOnAppear { listFocused = true } }
    }
}

/// Records whether `dsCursorRing` draws on this item, by the same rule.
private struct CursorProbe: View {
    let index: Int
    let isCurrent: Bool
    let log: RingLog
    @Environment(\.dsFocusContainerShowsCursor) private var showsCursor

    var body: some View {
        let _ = { log.cursorRing[index] = isCurrent && showsCursor }()
        Color.clear
    }
}

/// Type-erases the two DS styles the probe list mixes.
private struct AnyButtonStyle: ButtonStyle {
    private let make: (Configuration) -> AnyView
    init<S: ButtonStyle>(_ style: S) { make = { AnyView(style.makeBody(configuration: $0)) } }
    func makeBody(configuration: Configuration) -> some View { make(configuration) }
}

/// UI audit T4 hand re-check: with Keyboard navigation on, a focused list rang
/// every DS control inside it, a button Tab landed on never rang, and the
/// arrows moved a cursor from a focused button. Real key events into one
/// in-process window; nothing reaches other apps.
@Suite("Focusable containers in a window", .serialized)
@MainActor
struct FocusContainerWindowTests {
    private func press(_ characters: String, keyCode: UInt16, in window: NSWindow) {
        for type in [NSEvent.EventType.keyDown, .keyUp] {
            guard let event = NSEvent.keyEvent(
                with: type, location: .zero, modifierFlags: [], timestamp: 0,
                windowNumber: window.windowNumber, context: nil,
                characters: characters, charactersIgnoringModifiers: characters,
                isARepeat: false, keyCode: keyCode
            ) else { continue }
            window.sendEvent(event)
        }
    }

    private func open(_ log: RingLog, focusOnAppear: Bool) async throws -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 300, height: 300),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = NSHostingView(rootView: ProbeList(log: log, focusOnAppear: focusOnAppear))
        window.makeKeyAndOrderFront(nil)
        try await Task.sleep(for: .milliseconds(300))
        return window
    }

    @Test("a list holding the focus rings only its current item, not every control inside")
    func onlyTheCursorItemRings() async throws {
        let log = RingLog()
        let window = try await open(log, focusOnAppear: true)
        defer { window.close() }
        try #require(log.containerFocused, "the list never took the focus")
        #expect(log.styleFocus.values.allSatisfy { !$0 }, "a control inherits the list's focus: \(log.styleFocus)")
        #expect(log.cursorRing == [0: true, 1: false, 2: false])
        #expect(log.rings == 1)
    }

    @Test("Tab: the list's stop rings its current item; a button inside rings alone and the arrows leave the cursor")
    func tabStops() async throws {
        guard NSApplication.shared.isFullKeyboardAccessEnabled else { return }
        let log = RingLog()
        let window = try await open(log, focusOnAppear: false)
        defer { window.close() }
        var sawList = false, sawItem = false
        for _ in 0..<8 {
            press("\t", keyCode: 48, in: window)
            try await Task.sleep(for: .milliseconds(150))
            #expect(log.styleFocus.values.allSatisfy { !$0 }, "inherited focus: \(log.styleFocus)")
            if log.containerFocused {
                sawList = true
                #expect(log.rings == 1, "list stop: \(log.cursorRing)")
            }
            if let focused = log.ownFocus.first(where: { $0.value })?.key {
                sawItem = true
                // The button has the system ring; the cursor ring is down.
                #expect(!log.containerFocused)
                #expect(log.rings == 0, "item \(focused) stop: \(log.cursorRing)")
                // ↓ a focused button doesn't use reaches the list's handler, which must ignore it.
                press("\u{F701}", keyCode: 125, in: window)
                try await Task.sleep(for: .milliseconds(100))
                #expect(log.cursorRing.values.allSatisfy { !$0 })
            }
        }
        #expect(sawList && sawItem, "Tab never reached \(sawList ? "a button inside" : "the list")")
        // Back on the list's own stop, the cursor is where the list left it: ↓ above moved nothing.
        for _ in 0..<8 where !log.containerFocused {
            press("\t", keyCode: 48, in: window)
            try await Task.sleep(for: .milliseconds(150))
        }
        try #require(log.containerFocused)
        #expect(log.cursorRing == [0: true, 1: false, 2: false])
        press("\u{F701}", keyCode: 125, in: window)
        try await Task.sleep(for: .milliseconds(150))
        #expect(log.cursorRing == [0: false, 1: true, 2: false], "↓ on the list's own stop moves the cursor")
    }
}
