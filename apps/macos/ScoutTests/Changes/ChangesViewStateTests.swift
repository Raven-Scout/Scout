import SwiftUI
import Testing
@testable import Scout

@MainActor
@Suite("Changes tab states", .serialized)
struct ChangesViewStateTests {

    // MARK: - Helpers

    /// The changes fixture with its top-level `verified` / `calibrating` flags set.
    private static func fixtureJSON(verified: Bool, calibrating: Bool) throws -> String {
        let raw = try ChangesContractTests.data("changes")
        var object = try #require(try JSONSerialization.jsonObject(with: raw) as? [String: Any])
        object["verified"] = verified
        object["calibrating"] = calibrating
        let data = try JSONSerialization.data(withJSONObject: object)
        return String(decoding: data, as: UTF8.self)
    }

    private static func row(governance: Bool = false, state: String = "done", verdict: String? = "helped",
                            markedWrong: Bool = false, rolledBackBy: String? = nil) throws -> ChangeRow {
        let test: [String: Any?] = ["state": state, "verdict": verdict, "marked_wrong": markedWrong,
                                    "rolled_back_by": rolledBackBy]
        let object: [String: Any] = [
            "id": "chg-000000000001", "kind": "phase", "summary": "A change", "commit": "abc1234",
            "ts": "2026-11-16T12:00:00+00:00", "governance": governance,
            "test": test.compactMapValues { $0 },
        ]
        let data = try JSONSerialization.data(withJSONObject: object)
        return try JSONDecoder().decode(ChangeRow.self, from: data)
    }

    private static func shownText(verified: Bool, calibrating: Bool) async throws -> String {
        let service = try await LoadedChangesService.make(
            changesJSON: try fixtureJSON(verified: verified, calibrating: calibrating))
        let view = ChangesView()
            .environmentObject(service)
            .environmentObject(LoadedChangesService.decisions())
        let nodes = await AccessibilityTree.nodes(of: view.frame(width: 900), size: CGSize(width: 900, height: 1200))
        return nodes.flatMap { [$0.label, $0.value] }.filter { !$0.isEmpty }.joined(separator: "\n")
    }

    // MARK: - Tests

    @Test("an unverified history turns Undo off for every row and shows the banner")
    func unverifiedDisablesUndoAndShowsBanner() async throws {
        let changes = try ChangesContractTests.changes()
        #expect(changes.verified == false)
        #expect(!changes.changes.isEmpty)
        for row in changes.changes { #expect(!ChangeActions.canUndo(row, verified: changes.verified)) }

        let shown = try await Self.shownText(verified: false, calibrating: false)
        #expect(shown.contains(ChangesView.unverifiedBanner), "unverified banner missing:\n\(shown)")
        #expect(!shown.contains(ChangesView.calibratingBanner))
    }

    @Test("a confirmed, settled history shows no banner; a calibrating one shows only that banner")
    func calibratingBanner() async throws {
        let settled = try await Self.shownText(verified: true, calibrating: false)
        #expect(!settled.contains(ChangesView.unverifiedBanner))
        #expect(!settled.contains(ChangesView.calibratingBanner))

        let calibrating = try await Self.shownText(verified: true, calibrating: true)
        #expect(calibrating.contains(ChangesView.calibratingBanner), "calibrating banner missing:\n\(calibrating)")
        #expect(!calibrating.contains(ChangesView.unverifiedBanner))
    }

    @Test("a governance change or an untested one can never be undone")
    func governanceAndUntestedNeverUndo() throws {
        #expect(ChangeActions.canUndo(try Self.row(), verified: true))
        #expect(!ChangeActions.canUndo(try Self.row(governance: true), verified: true))
        #expect(!ChangeActions.canUndo(try Self.row(state: "not-tested", verdict: nil), verified: true))
        #expect(ChangeActions.canUndo(try Self.row(state: "testing", verdict: nil), verified: true))
    }

    @Test("a rolled-back row cannot be undone again")
    func rolledBackRowCannotBeUndoneAgain() throws {
        #expect(!ChangeActions.canUndo(try Self.row(rolledBackBy: "chg-000000000009"), verified: true))
    }

    @Test("mark wrong needs a verdict and works once")
    func markWrongOnlyOnceAndOnlyWithAVerdict() throws {
        #expect(ChangeActions.canMarkWrong(try Self.row()))
        #expect(!ChangeActions.canMarkWrong(try Self.row(verdict: nil)))
        #expect(!ChangeActions.canMarkWrong(try Self.row(markedWrong: true)))
    }
}
