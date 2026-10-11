import AppKit
import SwiftUI
import Testing
@testable import Scout

@MainActor
private final class AskLog {
    var asked: [String] = []
}

@MainActor
private struct TwoRows: View {
    let log: AskLog
    @State private var first = false
    @State private var second = false
    @State private var search = ""

    var body: some View {
        VStack {
            TextField("Search", text: $search)
            ScrollView {
                VStack {
                    DisclosureRow(height: 40, label: "First", expanded: $first, onAsk: { log.asked.append("First") }) { _ in
                        HStack {
                            Text("First")
                            Button("Link") {}
                        }
                    }
                    DisclosureRow(height: 40, label: "Second", expanded: $second, onAsk: { log.asked.append("Second") }) { _ in
                        Text("Second")
                    }
                }
            }
        }
        .frame(width: 400, height: 300)
    }
}

/// Sends key events to one in-process window; nothing reaches other apps.
@Suite("DisclosureRow keyboard focus", .serialized)
@MainActor
struct DisclosureRowFocusTests {
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

    private func askedAfterTabs(_ tabs: Int) async throws -> [String] {
        let log = AskLog()
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 400, height: 300),
            styleMask: [.titled], backing: .buffered, defer: false)
        defer { window.close() }
        window.contentView = NSHostingView(rootView: TwoRows(log: log))
        window.makeKeyAndOrderFront(nil)
        try await Task.sleep(for: .milliseconds(300))
        for _ in 0..<tabs {
            press("\t", keyCode: 48, in: window)
            try await Task.sleep(for: .milliseconds(100))
        }
        press("\r", keyCode: 36, in: window)
        try await Task.sleep(for: .milliseconds(150))
        return log.asked
    }

    @Test func tabReachesTheRowAfterARowWithButtons() async throws {
        guard NSApplication.shared.isFullKeyboardAccessEnabled else { return }
        var reached: [String] = []
        for tabs in 1...6 {
            reached += try await askedAfterTabs(tabs)
        }
        #expect(reached.contains("First"))
        #expect(reached.contains("Second"))
    }
}
