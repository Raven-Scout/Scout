import AppKit
import Foundation
import SwiftUI
import Testing
@testable import Scout

/// `VaultUndoController` bridges async vault writes to the window's
/// synchronous `UndoManager` (C10).
@Suite("Vault undo controller")
@MainActor
struct VaultUndoControllerTests {
    /// Counts what the step's closures did, and can be told to fail.
    @MainActor
    final class Probe {
        var log: [String] = []
        var failUndo = false
        var refreshes = 0
        func step(_ name: String = "Mark Done") -> UndoStep {
            UndoStep(
                actionName: name,
                undo: { [self] in
                    if failUndo { throw VaultWriteError.undoStale(file: "action-items-2026-04-20.md") }
                    log.append("undo")
                },
                redo: { [self] in log.append("redo") }
            )
        }
    }

    /// An undo manager outside any run loop event: groups are opened by hand.
    private func manager() -> UndoManager {
        let m = UndoManager()
        m.groupsByEvent = false
        return m
    }

    private func register(_ c: VaultUndoController, _ p: Probe, _ m: UndoManager, name: String = "Mark Done") {
        m.beginUndoGrouping()
        c.register(p.step(name), with: m) { p.refreshes += 1 }
        m.endUndoGrouping()
    }

    @Test func registeringNamesTheEditMenuItem() {
        let c = VaultUndoController(), p = Probe(), m = manager()
        register(c, p, m)
        #expect(m.canUndo)
        #expect(m.undoActionName == "Mark Done")
        #expect(c.latest?.actionName == "Mark Done")
    }

    @Test func undoRunsTheStepThenRedoRunsItAgain() async {
        let c = VaultUndoController(), p = Probe(), m = manager()
        register(c, p, m)

        m.undo()
        await c.lastRun?.value
        #expect(p.log == ["undo"])
        #expect(m.canRedo)
        #expect(m.redoActionName == "Mark Done")

        m.redo()
        await c.lastRun?.value
        #expect(p.log == ["undo", "redo"])
        #expect(m.canUndo)
        #expect(p.refreshes == 2)
    }

    @Test func aRefusedUndoReportsAProblemAndLeavesNothingToRedo() async {
        let c = VaultUndoController(), p = Probe(), m = manager()
        p.failUndo = true
        register(c, p, m)

        m.undo()
        await c.lastRun?.value
        #expect(p.log.isEmpty)
        #expect(c.problem?.kind == .undoStale)
        #expect(c.problem?.title == "Couldn't undo mark done")
        // The optimistic redo entry is withdrawn: the stacks no longer
        // describe the file.
        #expect(!m.canRedo)
        // The page still reloads, so it shows the real file.
        #expect(p.refreshes == 1)
    }

    @Test func toastUndoGoesThroughTheManagerWhenItIsOnTop() async {
        let c = VaultUndoController(), p = Probe(), m = manager()
        register(c, p, m)

        c.undoLatest(with: m)
        await c.lastRun?.value
        #expect(p.log == ["undo"])
        #expect(m.canRedo)
        #expect(c.latest == nil)
    }

    @Test func toastUndoOfAnOlderActionReversesJustThatOne() async {
        let c = VaultUndoController(), first = Probe(), second = Probe(), m = manager()
        register(c, first, m, name: "Snooze")
        let older = c.latest
        register(c, second, m, name: "Approve")
        #expect(c.latest != older)

        c.undoLatest(with: m)   // the toast shows the newest: Approve
        await c.lastRun?.value
        #expect(second.log == ["undo"])
        #expect(first.log.isEmpty)
    }

    @Test func toastUndoWorksWithoutAnUndoManager() async {
        let c = VaultUndoController(), p = Probe()
        c.register(p.step(), with: nil) { p.refreshes += 1 }
        c.undoLatest(with: nil)
        await c.lastRun?.value
        #expect(p.log == ["undo"])
    }

    /// Pages register with `@Environment(\.undoManager)`; Edit ▸ Undo uses the
    /// window's manager. They must be the same object, or ⌘Z never sees the
    /// app's entries.
    @Test func theEnvironmentUndoManagerIsTheWindows() {
        final class Box { var manager: UndoManager? }
        struct Probe: View {
            let box: Box
            @Environment(\.undoManager) private var undoManager
            var body: some View {
                box.manager = undoManager
                return Text("x")
            }
        }
        let box = Box()
        let host = NSHostingView(rootView: Probe(box: box))
        host.frame = CGRect(x: 0, y: 0, width: 200, height: 100)
        let window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: true)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        _ = host.fittingSize
        #expect(box.manager != nil)
        #expect(box.manager === window.undoManager)
    }

    @Test func feedbackOverlayRendersToastAndProblem() {
        let c = VaultUndoController(), p = Probe()
        c.register(p.step("Approve"), with: nil) {}
        c.problem = WriteProblem.from(VaultWriteError.undoStale(file: "p.md"), action: "undo approve")
        ViewHost.render(WriteFeedbackOverlay(controller: c), size: CGSize(width: 600, height: 300))
        #expect(WriteFeedbackOverlay.doneLine(for: "Approve") == "Proposal approved")
    }

    @Test func undoStaysAvailableAfterManyLaterActions() async {
        // The first action's token must stay alive while its menu entry does.
        let c = VaultUndoController(), m = manager()
        let probes = (0..<10).map { _ in Probe() }
        for p in probes { register(c, p, m) }
        for _ in probes {
            m.undo()
            await c.lastRun?.value
        }
        #expect(probes.allSatisfy { $0.log == ["undo"] })
    }
}
