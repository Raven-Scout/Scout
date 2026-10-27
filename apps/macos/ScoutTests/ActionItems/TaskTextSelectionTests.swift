import AppKit
import SwiftUI
import Testing
@testable import Scout

/// Mounts real cards in one in-process window and copies through the responder
/// chain, the way Edit ▸ Copy does. It finds selectable text by behaviour (a view
/// that takes focus and exposes its text), not by SwiftUI's private class names.
///
/// SwiftUI exposes selectable text that way on macOS 27 but not on macOS 15.7,
/// where these tests find no selectable view at all, so they run on macOS 27
/// and later (the Xcode 27 CI job and local runs).
@Suite(
    "Task text selection", .serialized,
    .enabled(
        if: ProcessInfo.processInfo.isOperatingSystemAtLeast(
            OperatingSystemVersion(majorVersion: 27, minorVersion: 0, patchVersion: 0)),
        "selectable text is a focusable view only from macOS 27"))
@MainActor
struct TaskTextSelectionTests {
    private final class KeyWindow: NSWindow {
        override var canBecomeKey: Bool { true }
    }

    private func mount<V: View>(_ view: V, size: CGSize = CGSize(width: 820, height: 700)) async throws -> NSWindow {
        let window = KeyWindow(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = NSHostingView(rootView: view.frame(width: size.width, height: size.height, alignment: .topLeading))
        window.makeKeyAndOrderFront(nil)
        try await Task.sleep(for: .milliseconds(300))
        return window
    }

    private func text(of view: NSView) -> String {
        let value = view.accessibilityValue()
        return (value as? String) ?? (value as? NSAttributedString)?.string ?? ""
    }

    private func selectableViews(in window: NSWindow) -> [NSView] {
        func all(_ v: NSView) -> [NSView] { [v] + v.subviews.flatMap(all) }
        guard let root = window.contentView else { return [] }
        return all(root).filter { $0.acceptsFirstResponder && !text(of: $0).isEmpty }
    }

    /// Waits up to 3 s for every needle to show up in selectable text, since a
    /// loaded CI runner can need more than one layout pass, then returns all of it.
    private func selectableTexts(in window: NSWindow, waitingFor needles: [String]) async throws -> [String] {
        var texts: [String] = []
        for _ in 0..<30 {
            window.contentView?.layoutSubtreeIfNeeded()
            texts = selectableViews(in: window).map(text(of:))
            if needles.allSatisfy({ needle in texts.contains { $0.contains(needle) } }) { break }
            try await Task.sleep(for: .milliseconds(100))
        }
        return texts
    }

    /// Selects all of `view`'s text and sends Copy, then reads the pasteboard.
    private func copyAll(_ view: NSView, in window: NSWindow) async throws -> String? {
        let pasteboard = NSPasteboard.general
        let saved = pasteboard.string(forType: .string)
        defer {
            pasteboard.clearContents()
            if let saved { pasteboard.setString(saved, forType: .string) }
        }
        pasteboard.clearContents()
        window.makeFirstResponder(view)
        _ = view.tryToPerform(#selector(NSResponder.selectAll(_:)), with: nil)
        _ = view.tryToPerform(#selector(NSText.copy(_:)), with: nil)
        try await Task.sleep(for: .milliseconds(100))
        return pasteboard.string(forType: .string)
    }

    @Test func anOpenCardsBodySubBulletsAndCommentsCanBeCopied() async throws {
        let task = SmokeFixtures.task(comments: SmokeFixtures.comments, details: SmokeFixtures.details)
        let window = try await mount(TaskCardView(
            task: task, kind: .urgent, displayedDate: SmokeFixtures.day,
            scoutDirectory: URL(fileURLWithPath: "/tmp/scout"), onOp: { _, _ in }))
        defer { window.close() }

        let texts = try await selectableTexts(in: window, waitingFor: [
            "RFC by Friday", "Priya opens this tracker on Tuesday.", "Saw three alerts in ten minutes.",
        ])
        #expect(texts.contains { $0.contains("RFC by Friday") })
        #expect(texts.contains("Priya opens this tracker on Tuesday."))
        #expect(texts.contains("Saw three alerts in ten minutes."))
        // A click on the title opens and closes the card, so it stays a click target.
        #expect(!texts.contains { $0.contains("Reply to Priya") })

        let comment = try #require(selectableViews(in: window).first { text(of: $0) == "Saw three alerts in ten minutes." })
        #expect(try await copyAll(comment, in: window) == "Saw three alerts in ten minutes.")
    }

    @Test func aBoardCardsSubjectCanBeCopied() async throws {
        let window = try await mount(
            BoardCardView(task: SmokeFixtures.task(), kind: .todo, scoutDirectory: URL(fileURLWithPath: "/tmp/scout")),
            size: CGSize(width: 320, height: 200))
        defer { window.close() }

        _ = try await selectableTexts(in: window, waitingFor: ["Reply to Priya"])
        let subject = try #require(selectableViews(in: window).first { text(of: $0).contains("Reply to Priya") })
        #expect(try await copyAll(subject, in: window)?.contains("Reply to Priya") == true)
    }

    @Test func aTopicStepsDetailCanBeCopied() async throws {
        let body = "Walk Sam through the rollout checklist before Thursday, then hand the dashboard to Priya."
        let task = SmokeFixtures.task(body: body, details: SmokeFixtures.details)
        let window = try await mount(StepDetailView(
            task: task, rootSession: nil, prompt: { "" }, scoutDirectory: URL(fileURLWithPath: "/tmp/scout")))
        defer { window.close() }

        let texts = try await selectableTexts(in: window, waitingFor: [body, "Priya opens this tracker on Tuesday."])
        #expect(texts.contains(body))
        #expect(texts.contains("Priya opens this tracker on Tuesday."))
    }
}
