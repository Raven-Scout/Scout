import Foundation
import SwiftUI
import Testing
@testable import Scout

@MainActor
@Suite("Change cards and detail", .serialized)
struct ChangeDetailTests {
    private static let undoID = "chg-000000000001"    // testing, not governance, not rolled back
    private static let wrongID = "chg-000000000000"   // has a verdict, not marked wrong
    private static let oldID = "chg-000000000006"     // done and already marked wrong

    private var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    /// The changes fixture with `verified` set, and some rows shown rolled back.
    private func changesJSON(verified: Bool, rolledBack: [String: String] = [:]) throws -> String {
        var object = try #require(JSONSerialization.jsonObject(with: ChangesContractTests.data("changes")) as? [String: Any])
        object["verified"] = verified
        var rows = try #require(object["changes"] as? [[String: Any]])
        for index in rows.indices {
            guard let id = rows[index]["id"] as? String, let by = rolledBack[id],
                  var test = rows[index]["test"] as? [String: Any] else { continue }
            test["rolled_back_by"] = by
            rows[index]["test"] = test
        }
        object["changes"] = rows
        return String(decoding: try JSONSerialization.data(withJSONObject: object), as: UTF8.self)
    }

    private final class Box: @unchecked Sendable {
        var changes: String
        init(_ changes: String) { self.changes = changes }
    }

    /// Holds an engine answer until the test lets it go.
    private actor Gate {
        private var open = false
        private var waiters: [CheckedContinuation<Void, Never>] = []
        func wait() async { if open { return }; await withCheckedContinuation { waiters.append($0) } }
        func release() { open = true; waiters.forEach { $0.resume() }; waiters = [] }
    }

    private struct Rig {
        let runner: RuleBasedRunner
        let service: ChangesService
        let decisions: ChangesDecisions
        let box: Box
    }

    /// A loaded service and a decisions object on one runner. `box.changes` is what the next refresh reads.
    private func rig(verified: Bool = true, undoStderr: String = "", undoExit: Int32 = 0, gate: Gate? = nil, diff: String = "--- a/x\n+++ b/x\n-old\n+new\n") async throws -> Rig {
        let runner = RuleBasedRunner()
        let box = Box(try changesJSON(verified: verified))
        func fixture(_ name: String) throws -> String { String(decoding: try ChangesContractTests.data(name), as: UTF8.self) }
        runner.on(tool: "scoutctl", prefix: ["changes", "digest"], stdout: try fixture("changes-digest"))
        runner.on({ url, args in url.lastPathComponent == "scoutctl" && args.prefix(2) == ["changes", "--json"] }) { _, _, _ in
            ProcessResult(exitCode: 0, stdout: Data(box.changes.utf8), stderr: Data())
        }
        runner.on(tool: "scoutctl", prefix: ["engagement", "--json"], stdout: try fixture("engagement"))
        runner.on({ url, args in url.lastPathComponent == "scoutctl" && args.prefix(2) == ["changes", "undo"] }) { _, _, _ in
            if let gate { await gate.wait() }
            return ProcessResult(exitCode: undoExit, stdout: Data(), stderr: Data(undoStderr.utf8))
        }
        runner.on(tool: "scoutctl", prefix: ["changes", "wrong"])
        runner.on({ url, args in url.lastPathComponent == "scoutctl" && args.prefix(2) == ["changes", "diff"] }) { _, _, _ in
            if let gate { await gate.wait() }
            return ProcessResult(exitCode: 0, stdout: Data(diff.utf8), stderr: Data())
        }
        let exe = URL(fileURLWithPath: "/usr/bin/scoutctl")
        let service = ChangesService(
            scoutctl: { exe }, runner: runner,
            logsDirectory: URL(fileURLWithPath: "/tmp/none-\(UUID().uuidString)"),
            pendingDirectory: URL(fileURLWithPath: "/tmp/none-\(UUID().uuidString)"), fileEvents: NoopFS())
        await service.refresh()
        return Rig(runner: runner, service: service, decisions: ChangesDecisions(runner: runner, scoutctl: { exe }), box: box)
    }

    private func refreshCount(_ runner: RuleBasedRunner) -> Int {
        runner.calls(to: "scoutctl").filter { $0.prefix(2) == ["changes", "--json"] }.count
    }

    private func changeRow(_ id: String, in service: ChangesService) throws -> ChangeRow {
        try #require(service.snapshot?.changes.changes.first { $0.id == id })
    }

    private func engagementDays(_ range: ClosedRange<Int>) throws -> [EngagementDay] {
        let json = "[" + range.map { day in
            String(format: "{\"date\":\"2026-11-%02d\",\"feedback\":%d,\"reliance\":1,\"noise\":0}", day, day)
        }.joined(separator: ",") + "]"
        return try JSONDecoder().decode([EngagementDay].self, from: Data(json.utf8))
    }

    // MARK: - Pure model

    @Test func sparklineSplitsAtTheChange() throws {
        let days = try engagementDays(1...30)
        let ts = try #require(ChangesDecoding.time("2026-11-15T12:00:00+00:00"))
        let spark = DetailModel.sparkline(days: days, around: ts, calendar: utc)
        // Engagement is feedback + reliance (1 a day). 14 days before the 15th, and 14 from the 15th on.
        #expect(spark.before == Array(1...14).map { $0 + 1 })
        #expect(spark.after == Array(15...28).map { $0 + 1 })
    }

    @Test func sparklineDoesNotInventMissingDays() throws {
        let days = try engagementDays(10...16)
        let ts = try #require(ChangesDecoding.time("2026-11-15T12:00:00+00:00"))
        let spark = DetailModel.sparkline(days: days, around: ts, calendar: utc)
        #expect(spark.before.count == 5)
        #expect(spark.after.count == 2)
        let none = DetailModel.sparkline(days: [], around: ts, calendar: utc)
        #expect(none.before.isEmpty && none.after.isEmpty)
    }

    @Test func wouldHaveDoneLinesOnlyWhileCalibrating() {
        let calibrating = ChangeTest(calibrating: true, wouldHaveDone: ["targeted": true, "one_at_a_time": false])
        #expect(DetailModel.wouldHaveDoneLines(calibrating) == [
            "Targeted would have rolled this back.",
            "One-at-a-time would have left this alone.",
        ])
        let settled = ChangeTest(calibrating: false, wouldHaveDone: ["targeted": true, "one_at_a_time": true])
        #expect(DetailModel.wouldHaveDoneLines(settled).isEmpty)
        #expect(DetailModel.wouldHaveDoneLines(ChangeTest(calibrating: true)).isEmpty)   // nothing recorded
    }

    @Test func overlapLine() {
        #expect(DetailModel.overlapLine(ChangeTest(overlap: ["a", "b"])) == "2 changes landed in this window; the effect is shared")
        #expect(DetailModel.overlapLine(ChangeTest(overlap: ["a"])) == "1 change landed in this window; the effect is shared")
        #expect(DetailModel.overlapLine(ChangeTest()) == nil)
    }

    @Test func inferredWhyIsMarked() {
        let guessed = ChangeWhy(type: "pattern", ref: "pattern-288", gloss: "A mistake that kept recurring", inferred: true)
        #expect(DetailModel.gloss(guessed) == "A mistake that kept recurring (guessed from the commit)")
        let known = ChangeWhy(type: "pattern", ref: "pattern-288", gloss: "A mistake that kept recurring", inferred: false)
        #expect(DetailModel.gloss(known) == "A mistake that kept recurring")
        #expect(VerdictPill.spokenLabel(ChangeTest(), inferred: true).contains("guessed"))
        #expect(!VerdictPill.spokenLabel(ChangeTest(), inferred: false).contains("guessed"))
    }

    @Test func anEmptyGlossIsNeverReplacedByTheReference() {
        let why = ChangeWhy(type: "pattern", ref: "pattern-288", gloss: "  ", inferred: true)
        #expect(DetailModel.gloss(why) == "Reason not recorded")
        #expect(!DetailModel.gloss(why).contains("pattern-288"))
    }

    @Test func verdictPillUsesTheTrendTones() {
        #expect(String(describing: VerdictPill.tone(ChangeTest(state: "done", verdict: "helped"))) == "ok")
        #expect(String(describing: VerdictPill.tone(ChangeTest(state: "done", verdict: "hurt"))) == "err")
        #expect(String(describing: VerdictPill.tone(ChangeTest(state: "done", verdict: "rolled back"))) == "err")
        #expect(String(describing: VerdictPill.tone(ChangeTest(state: "done", verdict: "no clear effect"))) == "neutral")
        #expect(String(describing: VerdictPill.tone(ChangeTest(state: "done", verdict: "can't tell"))) == "neutral")
        #expect(String(describing: VerdictPill.tone(ChangeTest(state: "testing"))) == "warn")
        #expect(String(describing: VerdictPill.tone(ChangeTest())) == "neutral")
        #expect(VerdictPill.words(ChangeTest(state: "testing")) == "testing")
        #expect(VerdictPill.words(ChangeTest()) == "not tested")
        #expect(VerdictPill.words(ChangeTest(state: "done", verdict: "helped")) == "helped")
    }

    // MARK: - The list

    @Test func cardsRunNewestFirst() async throws {
        let service = try await LoadedChangesService.make()
        let rows = ChangeCard.newestFirst(try #require(service.snapshot).changes.changes)
        #expect(rows.map(\.ts) == rows.map(\.ts).sorted(by: >))
        #expect(rows.first?.summary == "A rollback change")
        #expect(rows.last?.summary == "A wishlist-done change")
    }

    // MARK: - Links

    @Test func patternLinkOpensTheMistakeAudit() throws {
        let why = ChangeWhy(type: "pattern", ref: "pattern-288", gloss: "g", inferred: false)
        let links = DetailModel.links(for: why, vault: nil)
        #expect(links == [.note(label: "Open the mistake audit", path: "knowledge-base/scout-mistake-audit.md")])

        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("ChangeDetail-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmp) }
        let state = AppState(configuration: .testing(scoutDirectory: tmp))
        var opened: URL?
        links[0].perform(openNote: { state.requestOpenKBNote($0) }, openFile: { opened = $0 })
        #expect(state.pendingKBNote == "knowledge-base/scout-mistake-audit.md")
        #expect(opened == nil)
    }

    @Test func wishlistLinkOnlyForAWellFormedSlugInsideTheVault() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("ChangeDetailVault-\(UUID().uuidString)")
        let wishlist = vault.appendingPathComponent("docs/wishlist")
        try FileManager.default.createDirectory(at: wishlist, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: vault) }
        try "x".write(to: wishlist.appendingPathComponent("red-carry.md"), atomically: true, encoding: .utf8)
        // A real file outside the wishlist folder, and a link inside it that points there.
        try "secret".write(to: vault.appendingPathComponent("outside.md"), atomically: true, encoding: .utf8)
        try FileManager.default.createSymbolicLink(
            at: wishlist.appendingPathComponent("escape.md"), withDestinationURL: vault.appendingPathComponent("outside.md"))

        func why(_ ref: String) -> ChangeWhy { ChangeWhy(type: "wishlist", ref: ref, gloss: "g", inferred: false) }
        let good = DetailModel.links(for: why("red-carry"), vault: vault)
        #expect(good.count == 1)
        if case .file(_, let url) = try #require(good.first) {
            #expect(url.lastPathComponent == "red-carry.md")
            #expect(url.resolvingSymlinksInPath().path.hasPrefix(vault.resolvingSymlinksInPath().path + "/docs/wishlist/"))
        } else {
            Issue.record("expected a file link")
        }
        for bad in ["../outside", "red-carry\n", "Red-Carry", "-red", "", "a/b", "red carry", "escape", "missing",
                    String(repeating: "a", count: 82)] {
            #expect(DetailModel.links(for: why(bad), vault: vault).isEmpty, "\(bad.debugDescription)")
        }
        #expect(DetailModel.links(for: why("red-carry"), vault: nil).isEmpty)
        #expect(DetailModel.links(for: ChangeWhy(type: "owner-ask", ref: "red-carry", gloss: "g", inferred: false), vault: vault).isEmpty)
        #expect(DetailModel.isWishlistSlug(String(repeating: "a", count: 81)))
    }

    // MARK: - Decisions

    @Test func undoAsksThenSendsTheID() async throws {
        let r = try await rig()
        let model = ChangeDetailModel()
        let row = try changeRow(Self.undoID, in: r.service)
        model.askToUndo(row, verified: true, pending: r.decisions.pendingUndos)
        #expect(model.confirmingUndoID == Self.undoID)
        // Asking sent nothing.
        #expect(r.runner.calls(to: "scoutctl").filter { $0.prefix(2) == ["changes", "undo"] }.isEmpty)

        let id = model.confirmUndo(presented: try #require(model.confirmingUndoID))
        await model.performUndo(id: id, decisions: r.decisions, service: r.service)
        let undos = r.runner.calls(to: "scoutctl").filter { $0.prefix(2) == ["changes", "undo"] }
        #expect(undos == [["changes", "undo", Self.undoID, "--surface", "app"]])
        // The service is refreshed once after the decision, which is why this isn't `calls.last`.
        #expect(refreshCount(r.runner) == 2)   // the first load, then the one after the decision
        #expect(model.confirmingUndoID == nil)
    }

    /// I6: SwiftUI may reset the dialog's binding (clearing `confirmingUndoID`) before the Undo action runs.
    /// The id the dialog was presented with still reaches the engine.
    @Test func theUndoReachesTheEngineEvenWhenTheDialogClearedItsBindingFirst() async throws {
        let r = try await rig()
        let model = ChangeDetailModel()
        model.askToUndo(try changeRow(Self.undoID, in: r.service), verified: true, pending: r.decisions.pendingUndos)
        let presented = try #require(model.confirmingUndoID)   // what `presenting:` captured
        model.cancelUndo()                                    // the binding's setter ran first
        #expect(model.confirmingUndoID == nil)
        let id = model.confirmUndo(presented: presented)
        await model.performUndo(id: id, decisions: r.decisions, service: r.service)
        #expect(r.runner.calls(to: "scoutctl").filter { $0.prefix(2) == ["changes", "undo"] }
                == [["changes", "undo", Self.undoID, "--surface", "app"]])
        #expect(r.decisions.pendingUndos == [Self.undoID])
    }

    @Test func cancellingTheQuestionSendsNothing() async throws {
        let r = try await rig()
        let model = ChangeDetailModel()
        model.askToUndo(try changeRow(Self.undoID, in: r.service), verified: true, pending: r.decisions.pendingUndos)
        model.cancelUndo()
        #expect(model.confirmingUndoID == nil)
        #expect(r.runner.calls(to: "scoutctl").filter { $0.prefix(2) == ["changes", "undo"] }.isEmpty)
    }

    @Test func undoIsOffUnlessTheHistoryIsVerified() async throws {
        let r = try await rig(verified: false)
        let model = ChangeDetailModel()
        let row = try changeRow(Self.undoID, in: r.service)
        #expect(!model.canUndo(row, verified: false, pending: r.decisions.pendingUndos))
        model.askToUndo(row, verified: false, pending: r.decisions.pendingUndos)
        #expect(model.confirmingUndoID == nil)
    }

    @Test func undoIsQueuedNotDoneAndStaysOffUntilTheRowShowsRolledBack() async throws {
        let r = try await rig()
        let model = ChangeDetailModel()
        let before = try changeRow(Self.undoID, in: r.service)
        #expect(model.canUndo(before, verified: true, pending: r.decisions.pendingUndos))
        model.askToUndo(before, verified: true, pending: r.decisions.pendingUndos)
        await model.performUndo(id: model.confirmUndo(presented: try #require(model.confirmingUndoID)), decisions: r.decisions, service: r.service)

        #expect(model.message == "Undo queued: the next dreaming run takes back the lines this change made.")
        #expect(!(model.message ?? "").lowercased().contains("undone"))
        #expect(r.decisions.pendingUndos == [Self.undoID])
        // The refresh found the row unchanged, so Undo stays off and the detail says it is queued.
        let after = try changeRow(Self.undoID, in: r.service)
        #expect(!model.canUndo(after, verified: true, pending: r.decisions.pendingUndos))
        let status = DetailModel.rollbackStatus(after, in: try #require(r.service.snapshot).changes.changes, pending: true)
        #expect(status == DetailModel.undoQueuedMessage)

        // A later refresh shows the row rolled back: the pending mark clears and the status reads the outcome.
        r.box.changes = try changesJSON(verified: true, rolledBack: [Self.undoID: "chg-000000000004"])
        await r.service.refresh()
        r.decisions.reconcilePendingUndos(with: r.service)
        #expect(r.decisions.pendingUndos.isEmpty)
        let rolled = try changeRow(Self.undoID, in: r.service)
        #expect(!model.canUndo(rolled, verified: true, pending: r.decisions.pendingUndos))   // ChangeActions: already reverted
        #expect(DetailModel.rollbackStatus(rolled, in: try #require(r.service.snapshot).changes.changes, pending: false)
                == "Rolled back by \u{201C}A rollback change\u{201D}.")
    }

    @Test func aRefusedUndoShowsTheEnginesWordsAndStaysAvailable() async throws {
        let r = try await rig(undoStderr: "scoutctl changes undo: a scheduled run may not make it\n", undoExit: 2)
        let model = ChangeDetailModel()
        let row = try changeRow(Self.undoID, in: r.service)
        model.askToUndo(row, verified: true, pending: r.decisions.pendingUndos)
        await model.performUndo(id: model.confirmUndo(presented: try #require(model.confirmingUndoID)), decisions: r.decisions, service: r.service)
        #expect(model.message == "Scout is running right now. Try again when the run finishes.")
        #expect(r.decisions.pendingUndos.isEmpty)
        #expect(model.canUndo(row, verified: true, pending: r.decisions.pendingUndos))
        #expect(refreshCount(r.runner) == 1)   // nothing changed, nothing re-read
    }

    @Test func markingWrongSendsTheIDAndRefreshesOnce() async throws {
        let r = try await rig()
        let model = ChangeDetailModel()
        let row = try changeRow(Self.wrongID, in: r.service)
        #expect(model.canMarkWrong(row))
        await model.markWrong(row, decisions: r.decisions, service: r.service)
        #expect(r.runner.calls(to: "scoutctl").filter { $0.prefix(2) == ["changes", "wrong"] }
                == [["changes", "wrong", Self.wrongID, "--surface", "app"]])
        #expect(refreshCount(r.runner) == 2)
        #expect(model.message == "Noted: this verdict is marked wrong.")
        // Already marked wrong, or no verdict yet: the button is off.
        #expect(!model.canMarkWrong(try changeRow(Self.oldID, in: r.service)))
        #expect(!model.canMarkWrong(try changeRow(Self.undoID, in: r.service)))
    }

    @Test func theDiffLoadsOnlyOnDemand() async throws {
        let r = try await rig()
        let model = ChangeDetailModel()
        #expect(model.diff == .idle)
        #expect(r.runner.calls(to: "scoutctl").filter { $0.prefix(2) == ["changes", "diff"] }.isEmpty)
        await model.loadDiff(for: try changeRow(Self.undoID, in: r.service), decisions: r.decisions)
        #expect(model.diff == .loaded("--- a/x\n+++ b/x\n-old\n+new\n"))
        #expect(r.runner.calls(to: "scoutctl").filter { $0.prefix(2) == ["changes", "diff"] }
                == [["changes", "diff", Self.undoID]])
        // A different change starts clean.
        model.open("chg-000000000002")
        #expect(model.diff == .idle)
    }

    // MARK: - Fix round 1

    @Test func aQueuedUndoSurvivesTheDetailModelAndTheTab() async throws {
        let r = try await rig()
        let first = ChangeDetailModel()
        let row = try changeRow(Self.undoID, in: r.service)
        first.askToUndo(row, verified: true, pending: r.decisions.pendingUndos)
        await first.performUndo(id: first.confirmUndo(presented: try #require(first.confirmingUndoID)), decisions: r.decisions, service: r.service)
        // The tab is left and reopened: a fresh model over the same decisions still has Undo off.
        let second = ChangeDetailModel()
        #expect(!second.canUndo(row, verified: true, pending: r.decisions.pendingUndos))
        second.askToUndo(row, verified: true, pending: r.decisions.pendingUndos)
        #expect(second.confirmingUndoID == nil)
    }

    @Test func aDiffThatArrivesAfterSwitchingChangeIsDropped() async throws {
        let gate = Gate()
        let r = try await rig(gate: gate)
        let model = ChangeDetailModel()
        model.open(Self.undoID)
        let a = try changeRow(Self.undoID, in: r.service)
        let load = Task { await model.loadDiff(for: a, decisions: r.decisions) }
        while model.diff != .loading { await Task.yield() }
        model.open(Self.wrongID)   // the owner moved on before the engine answered
        await gate.release()
        await load.value
        #expect(model.diff == .idle)
    }

    @Test func aMessageThatArrivesAfterSwitchingChangeIsDropped() async throws {
        let gate = Gate()
        let r = try await rig(gate: gate)
        let model = ChangeDetailModel()
        model.open(Self.undoID)
        let a = try changeRow(Self.undoID, in: r.service)
        model.askToUndo(a, verified: true, pending: r.decisions.pendingUndos)
        let id = model.confirmUndo(presented: try #require(model.confirmingUndoID))
        let run = Task { await model.performUndo(id: id, decisions: r.decisions, service: r.service) }
        while !model.busy { await Task.yield() }
        model.open(Self.wrongID)
        await gate.release()
        await run.value
        #expect(model.message == nil)
        #expect(r.decisions.pendingUndos == [Self.undoID])   // the decision itself is still remembered
    }

    // MARK: - What is on screen

    @Test func noBareIDsInTheDetail() async throws {
        let r = try await rig()
        let snapshot = try #require(r.service.snapshot)
        for row in snapshot.changes.changes {
            let view = ChangeDetailView(changeID: row.id, vaultDirectory: nil, model: ChangeDetailModel(),
                                        decisions: r.decisions, service: r.service,
                                        openNote: { _ in }, openFile: { _ in }, onClose: {})
            let nodes = await AccessibilityTree.nodes(of: view.frame(width: 700, height: 900), size: CGSize(width: 700, height: 900))
            let shown = nodes.flatMap { [$0.label, $0.value] }.filter { !$0.isEmpty }
            for text in shown {
                #expect(text.range(of: #"^chg-[0-9a-f]{12}$"#, options: .regularExpression) == nil, "bare id: \(text)")
                #expect(!text.contains("chg-"), "id in: \(text)")
                #expect(!text.contains("pattern-"), "reference in: \(text)")
            }
            #expect(shown.contains { $0.contains(row.summary) }, "summary missing for \(row.summary)")
        }
    }

    @Test func theDetailShowsTheOverlapAndTheCalibrationLines() async throws {
        let r = try await rig()
        let row = try changeRow(Self.wrongID, in: r.service)   // overlaps two changes; baseline measured
        let view = ChangeDetailView(changeID: row.id, vaultDirectory: nil, model: ChangeDetailModel(),
                                    decisions: r.decisions, service: r.service,
                                    openNote: { _ in }, openFile: { _ in }, onClose: {})
        let nodes = await AccessibilityTree.nodes(of: view.frame(width: 700, height: 900), size: CGSize(width: 700, height: 900))
        let text = nodes.flatMap { [$0.label, $0.value] }.joined(separator: "\n")
        #expect(text.contains("2 changes landed in this window; the effect is shared"), "\(text)")
        #expect(text.contains("Targeted would have left this alone."), "\(text)")
        #expect(text.contains("Rolled back by \u{201C}A rollback change\u{201D}."), "\(text)")
        #expect(text.contains("Open the mistake audit"), "\(text)")
        #expect(text.contains("This verdict is wrong"), "\(text)")
    }

    @Test func aCardNeverShowsAnIDAndMarksAGuessedReason() async throws {
        let service = try await LoadedChangesService.make()
        let snapshot = try #require(service.snapshot)
        let rows = snapshot.changes.changes
        let guessed = try #require(rows.first { $0.why.inferred })   // the null-gloss driver change
        for row in rows {
            let card = ChangeCard(row: row, days: snapshot.engagement.days, onOpen: {})
            let nodes = await AccessibilityTree.nodes(of: card.frame(width: 700), size: CGSize(width: 700, height: 200))
            let shown = nodes.flatMap { [$0.label, $0.value] }.filter { !$0.isEmpty }
            for text in shown { #expect(!text.contains("chg-"), "id in: \(text)") }
            #expect(shown.contains { $0.contains(row.summary) })
        }
        #expect(ChangeCard.spokenLabel(guessed).contains("Reason not recorded"))
        #expect(ChangeCard.spokenLabel(guessed).contains("reason guessed from the commit"))
    }

    @Test func theChangesTabListsEveryChangeAndOpensNone() async throws {
        let service = try await LoadedChangesService.make()
        let decisions = LoadedChangesService.decisions()
        let view = ChangesView().environmentObject(service).environmentObject(decisions)
        let nodes = await AccessibilityTree.nodes(of: view.frame(width: 900, height: 2400), size: CGSize(width: 900, height: 2400))
        let text = nodes.flatMap { [$0.label, $0.value] }.joined(separator: "\n")
        for summary in ["A rollback change", "A brain-edit change", "A driver change", "A wishlist-done change"] {
            #expect(text.contains(summary), "\(summary) missing")
        }
        #expect(!text.contains("chg-"))
    }
}
