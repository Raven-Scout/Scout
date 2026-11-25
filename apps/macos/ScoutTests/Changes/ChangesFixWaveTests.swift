import Foundation
import SwiftUI
import Testing
@testable import Scout

/// The final-review fix wave of the Changes tab: plain words for every reason and identifier, refusals the owner
/// can read, an empty diff, and a refresh that fails without taking the page away.
@MainActor
@Suite("Changes fix wave", .serialized)
struct ChangesFixWaveTests {
    private static let exe = URL(fileURLWithPath: "/usr/bin/scoutctl")
    private static let hash = String(repeating: "ab", count: 32)
    private static let undoID = "chg-000000000001"   // in the fixture: testing, not governance, not rolled back
    private static let doneID = "chg-000000000006"   // in the fixture: done, hurt, reasons ["recurrence-up"]

    private final class Flag: @unchecked Sendable { var on = false }

    private func fixture(_ name: String) throws -> String {
        String(decoding: try ChangesContractTests.data(name), as: UTF8.self)
    }

    /// A service on the contract fixtures and a decisions object on the same runner. While `broken.on`, the
    /// engagement read fails. `extra` answers anything else first.
    private func rig(broken: Flag = Flag(), verified: Bool = false, extra: (RuleBasedRunner) -> Void = { _ in }) throws
        -> (RuleBasedRunner, ChangesService, ChangesDecisions) {
        let runner = RuleBasedRunner()
        var changes = try fixture("changes")
        if verified {
            #expect(changes.contains(#""verified": false"#))
            changes = changes.replacingOccurrences(of: #""verified": false"#, with: #""verified": true"#)
        }
        extra(runner)
        let engagement = try fixture("engagement")
        runner.on({ url, args in url.lastPathComponent == "scoutctl" && args.prefix(2) == ["engagement", "--json"] }) { _, _, _ in
            broken.on ? ProcessResult(exitCode: 1, stdout: Data("not json".utf8), stderr: Data("boom".utf8))
                      : ProcessResult(exitCode: 0, stdout: Data(engagement.utf8), stderr: Data())
        }
        runner.on(tool: "scoutctl", prefix: ["changes", "digest"], stdout: try fixture("changes-digest"))
        runner.on(tool: "scoutctl", prefix: ["changes", "--json"], stdout: changes)
        let service = ChangesService(
            scoutctl: { Self.exe }, runner: runner,
            logsDirectory: URL(fileURLWithPath: "/tmp/none-\(UUID().uuidString)"),
            pendingDirectory: URL(fileURLWithPath: "/tmp/none-\(UUID().uuidString)"), fileEvents: NoopFS())
        return (runner, service, ChangesDecisions(runner: runner, scoutctl: { Self.exe }))
    }

    private func item(_ fields: String) throws -> NeedsOKItem {
        let json = """
        {"id":"gov-skip-red","summary":"Let the briefing skip a carried red item","path":"governance-pending/skip-red.md",
         "state":"awaiting-ok","diff_hash":"\(Self.hash)"\(fields.isEmpty ? "" : ",")\(fields)}
        """
        return try JSONDecoder().decode(NeedsOKItem.self, from: Data(json.utf8))
    }

    private func text(of view: some View, height: CGFloat = 600) async -> String {
        let nodes = await AccessibilityTree.nodes(of: view.frame(width: 700, height: height), size: CGSize(width: 700, height: height))
        return nodes.flatMap { [$0.label, $0.value] }.filter { !$0.isEmpty }.joined(separator: "\n")
    }

    // MARK: - I3: needs-OK why and targets in plain words

    @Test func theStripShowsTheWhyGlossNeverTheReference() async throws {
        let (_, service, decisions) = try rig()
        let glossed = try item(#""why":"pattern-288","why_gloss":"Long wrap messages nobody reads","governance":true"#)
        let strip = NeedsOKStrip(items: [glossed], vaultDirectory: nil, decisions: decisions, service: service)
        let shown = await text(of: strip, height: 300)
        #expect(shown.contains("Long wrap messages nobody reads"), "\(shown)")
        #expect(!shown.contains("pattern-288"), "\(shown)")
        // No gloss and a bare reference: nothing is shown rather than the reference; prose is shown as it is.
        let bare = try item(#""why":"pattern-288""#)
        let bareShown = await text(of: NeedsOKStrip(items: [bare], vaultDirectory: nil, decisions: decisions, service: service),
                                   height: 300)
        #expect(!bareShown.contains("pattern-288"), "\(bareShown)")
        let prose = try item(#""why":"The same red item was carried for six days.""#)
        let proseShown = await text(of: NeedsOKStrip(items: [prose], vaultDirectory: nil, decisions: decisions, service: service),
                                    height: 300)
        #expect(proseShown.contains("The same red item was carried for six days."), "\(proseShown)")
    }

    @Test func theReviewSheetShowsGlossedTargetsAndTheFilesContext() async throws {
        let (_, service, decisions) = try rig()
        let glossed = try item("""
            "why":"pattern-288","why_gloss":"Long wrap messages nobody reads",
            "targets":["pattern-288","engagement","briefing","pattern-7"],
            "targets_gloss":["Long wrap messages nobody reads","How much you use Scout",null,null],
            "context":"A red item nobody answers for six days is noise.","governance":true
            """)
        let sheet = GovernanceReviewSheet(item: glossed, vaultDirectory: nil, decisions: decisions, service: service)
        let shown = await text(of: sheet)
        #expect(shown.contains("It changes: Long wrap messages nobody reads, How much you use Scout, briefing, a recurring mistake"),
                "\(shown)")
        #expect(shown.contains("A red item nobody answers for six days is noise."), "\(shown)")
        #expect(!shown.contains("pattern-"), "\(shown)")
    }

    @Test func aDecisionOnAChangeThatIsNoLongerPendingSaysItIsGone() async throws {
        let (runner, service, decisions) = try rig(extra: { runner in
            runner.on(tool: "scoutctl", prefix: ["changes", "reject"],
                      stderr: "scoutctl changes reject: no pending governance change 'gov-skip-red'\n", exit: 2)
        })
        let model = GovernanceReviewModel(item: try item(#""why":"x y""#), vaultDirectory: nil,
                                          decisions: decisions, service: service)
        await model.reject()
        #expect(model.gone)
        #expect(model.message == nil)
        #expect(!model.canReject && !model.canApprove)
        #expect(runner.calls(to: "scoutctl").contains { $0.prefix(2) == ["changes", "--json"] })   // re-read once
    }

    // MARK: - m-c: a missing governance key is the engine's default

    @Test func aMissingGovernanceKeyIsAGovernanceChange() throws {
        #expect(try item("").governance == true)
        #expect(try item(#""governance":false"#).governance == false)
    }

    // MARK: - I4: verdict reasons in the owner's words

    @Test func everyEngineReasonHasOwnerWordsAndAnUnknownOneNeverShowsItsToken() {
        #expect(DetailModel.reasonWords(["noise-up", "recurrence-up", "named-negative"]) == [
            "More ignored DMs or stale 🔴s while engagement didn't rise",
            "The mistake it targeted happened more",
            "You replied negatively about it",
        ])
        #expect(DetailModel.reasonWords(["recurrence-zero", "engagement-up"]) == [
            "The mistake it targeted stopped happening", "You used Scout more, with no more noise",
        ])
        #expect(DetailModel.reasonWords(["away"]) == ["You were away for much of the test, so it can't tell"])
        let unknown = DetailModel.reasonWords(["drift-up", "", "chg-0123456789ab", "noise-up"])
        #expect(unknown == ["Another reason the test recorded", "More ignored DMs or stale 🔴s while engagement didn't rise"])
        for line in unknown { #expect(!line.contains("drift") && !line.contains("chg-")) }
    }

    @Test func theDetailDrawsTheVerdictReasonsOfTheFixtureRow() async throws {
        let (_, service, decisions) = try rig()
        await service.refresh()
        let row = try #require(service.snapshot?.changes.changes.first { $0.id == Self.doneID })
        #expect(row.test.reasons == ["recurrence-up"])
        let view = ChangeDetailView(changeID: row.id, vaultDirectory: nil, model: ChangeDetailModel(),
                                    decisions: decisions, service: service,
                                    openNote: { _ in }, openFile: { _ in }, onClose: {})
        let shown = await text(of: view, height: 1200)
        #expect(shown.contains("Why this verdict"), "\(shown)")
        #expect(shown.contains("The mistake it targeted happened more"), "\(shown)")
        #expect(!shown.contains("Recurrence up"), "\(shown)")
    }

    // MARK: - I7 + m-d: an undo the engine refuses

    @Test func anUndoTheEngineRefusesShowsItsReasonAndIsNotQueued() async throws {
        let reason = "Scout can't undo this change: it is the first commit in the vault's history."
        let (runner, service, decisions) = try rig(verified: true, extra: { runner in
            runner.on(tool: "scoutctl", prefix: ["changes", "undo"], stderr: "scoutctl changes undo: \(reason)\n", exit: 2)
        })
        await service.refresh()
        let row = try #require(service.snapshot?.changes.changes.first { $0.id == Self.undoID })
        let model = ChangeDetailModel()
        model.open(row.id)
        #expect(model.canUndo(row, verified: true, pending: decisions.pendingUndos))
        await model.performUndo(id: row.id, decisions: decisions, service: service)
        #expect(runner.calls(to: "scoutctl").contains(["changes", "undo", Self.undoID, "--surface", "app"]))
        #expect(model.message == reason)
        #expect(decisions.pendingUndos.isEmpty)
        #expect(model.canUndo(row, verified: true, pending: decisions.pendingUndos))
    }

    // MARK: - m-a: an empty diff

    @Test func anEmptyOrBlankDiffIsNoDiff() async throws {
        for printed in ["", "\n", "  \n\n"] {
            let (_, service, decisions) = try rig(extra: { runner in
                runner.on(tool: "scoutctl", prefix: ["changes", "diff"], stdout: printed)
            })
            await service.refresh()
            let row = try #require(service.snapshot?.changes.changes.first { $0.id == Self.undoID })
            let model = ChangeDetailModel()
            await model.loadDiff(for: row, decisions: decisions)
            #expect(model.diff == .loaded(""), "\(printed.debugDescription)")
        }
    }

    // MARK: - m-b: a failed refresh keeps the page

    @Test func aFailedRefreshKeepsTheLastGoodSnapshotAndTheTabSaysSo() async throws {
        let broken = Flag()
        let (_, service, decisions) = try rig(broken: broken)
        await service.refresh()
        let good = try #require(service.snapshot)
        broken.on = true
        await service.refresh()
        #expect(service.snapshot == good)   // the open review sheet hangs off this: it stays
        let page = ChangesView().environmentObject(service).environmentObject(decisions)
        let shown = await text(of: page, height: 2400)
        #expect(shown.contains("Couldn't refresh this page: The engine's engagement report couldn't be read."), "\(shown)")
        broken.on = false
        await service.refresh()
        let healed = await text(of: ChangesView().environmentObject(service).environmentObject(decisions), height: 2400)
        #expect(!healed.contains("Couldn't refresh this page"))
    }

    // MARK: - I5 (e): launch never waits on the Changes reads

    @Test func theLaunchChainDoesNotAwaitTheChangesRefresh() throws {
        let file = URL(fileURLWithPath: #filePath)   // …/apps/macos/ScoutTests/Changes/ChangesFixWaveTests.swift
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout/Shell/AppState.swift")
        let source = try String(contentsOf: file, encoding: .utf8)
        let awaited = source.split(separator: "\n").filter { $0.contains("await changesService.refresh()") }
        #expect(!awaited.isEmpty)
        for line in awaited {
            #expect(line.contains("Task {"), "the launch chain awaits the Changes refresh: \(line)")
        }
    }
}
