import Foundation
import Testing
@testable import Scout

/// `ScoutTests/Fixtures/{changes,engagement,changes-digest}.json` are byte copies of the
/// engine's generated `plugin/engine/tests/fixtures/contract/` files (`contract.yml`
/// compares them). Regenerate them from the engine, never by hand, then update the pins.
@Suite("Changes contract")
struct ChangesContractTests {
    private final class FixtureAnchor {}

    static func data(_ name: String) throws -> Data {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: name, withExtension: "json"))
        return try Data(contentsOf: url)
    }

    static func changes() throws -> ChangesDocument { try ChangesDocument.parse(data("changes")).get() }

    @Test func changesFixtureDecodes() throws {
        let doc = try Self.changes()
        #expect(doc.changes.count == 7)
        #expect(doc.needsOK.count == 2)
        #expect(doc.skippedRows == 0)
        #expect(doc.verified == false)
        #expect(doc.calibrating == true)
        #expect(doc.autoRollback == "off")
        #expect(doc.generatedAt == RunLedgerTime.parse("2026-11-16T12:00:00Z"))
        #expect(doc.needsOK[0].diffHash.count == 64)
        // The engine glosses the needs-OK why and targets, and passes the file's own explanation.
        let waiting = doc.needsOK[0], fallback = doc.needsOK[1]
        #expect(waiting.whyGloss == nil && waiting.whyLine == "The same red item was carried for six days with no reply.")
        #expect(waiting.targets == ["briefing", "pattern-249"] && waiting.targetsGloss == [nil, "Red items carried with no reply"])
        #expect(waiting.targetNames == ["briefing", "Red items carried with no reply"])
        #expect(waiting.contextLine == "A red item nobody answers for six days is noise, not a reminder.")
        #expect(fallback.why == "pattern-288" && fallback.whyLine == "A mistake that kept recurring")
        #expect(fallback.contextLine == nil && fallback.governance == false)
        #expect(Set(doc.changes.map(\.kind)) == ["rollback", "brain-edit", "driver", "kb-structure", "phase", "upgrade", "wishlist-done"])

        let tested = try #require(doc.changes.first { $0.id == "chg-000000000000" })
        #expect(tested.targets == ["pattern-288"])
        #expect(tested.why.gloss == "A mistake that kept recurring")
        #expect(tested.test.verdict == "rolled back")
        #expect(tested.test.rolledBackBy == "chg-000000000004")
        #expect(tested.test.reviewOn == "2026-11-29")
        #expect(tested.test.baseline?.days == 14)
        #expect(tested.test.baseline?.engagement == 0.6429)
        #expect(tested.test.baseline?.recurrences == ["pattern-288": 0])
        #expect(tested.test.overlap == ["chg-000000000001", "chg-000000000003"])
        #expect(tested.test.wouldHaveDone == ["one_at_a_time": false, "targeted": false])

        let rollback = try #require(doc.changes.first { $0.kind == "rollback" })
        #expect(rollback.reverts == "chg-000000000000")

        // A null gloss reads as an inferred, empty one.
        let inferred = try #require(doc.changes.first { $0.id == "chg-000000000001" })
        #expect(inferred.why.gloss.isEmpty && inferred.why.inferred)

        let wrong = try #require(doc.changes.first { $0.kind == "wishlist-done" })
        #expect(wrong.test.markedWrong && wrong.test.suspect)
        #expect(wrong.test.after?.recurrences == ["pattern-249": 2])
        #expect(doc.changes.first { $0.kind == "phase" }?.governance == true)
        #expect(doc.changes.first { $0.kind == "kb-structure" }?.backfilled == true)
    }

    @Test func engagementFixtureDecodes() throws {
        let doc = try EngagementDocument.parse(Self.data("engagement")).get()
        #expect(doc.days.count == 15)
        #expect(doc.skippedRows == 0)
        #expect(doc.totals["feedback"] == 5)
        #expect(doc.totals["noise"] == 2)
        #expect(doc.totals["reliance"] == 3)
        #expect(doc.pendingDMs == 1)
        let busy = try #require(doc.days.first { $0.date == "2026-11-12" })
        #expect(busy.feedback == 2 && busy.reliance == 0 && busy.noise == 0)
    }

    @Test func digestFixtureDecodes() throws {
        let digest = try ChangesDigest.parse(Self.data("changes-digest")).get()
        #expect(digest.weekOf == "2026-11-16")
        #expect(digest.verified == false)
        #expect(digest.needsOK == 1)
        #expect(digest.skippedRows == 0)
        #expect(digest.days.count == 7)
        #expect(digest.days.map(\.engagement) == [2, 2, 2, 0, 2, 0, 0])
        #expect(digest.days.map(\.noise) == [0, 0, 0, 1, 0, 1, 0])
        #expect(digest.engagement == DigestEngagement(feedback: 5, reliance: 3, thisWeek: 8, lastWeek: 0, changePct: nil))
        #expect(digest.noise == DigestNoise(thisWeek: 2, lastWeek: 0))
        #expect(digest.shipped.map(\.id) == ["chg-000000000004", "chg-000000000002", "chg-000000000005"])
        #expect(digest.testing.map(\.daysLeft) == [10, 12, 13])
        #expect(digest.testing.map(\.id) == ["chg-000000000003", "chg-000000000001", "chg-000000000000"])
        #expect(digest.learned.count == 1)
        #expect(digest.learned[0].verdict == "hurt" && digest.learned[0].suspect)
        #expect(digest.upNext == [DigestUpNext(slug: "gmail-empty-check", title: "Say so when a Gmail search finds nothing")])
    }

    @Test func newerSchemaIsUnsupported() {
        let newer = Data(#"{"schema_version": 2}"#.utf8)
        #expect(ChangesDocument.parse(newer) == .failure(.unsupportedSchema(2)))
        #expect(EngagementDocument.parse(newer) == .failure(.unsupportedSchema(2)))
        #expect(ChangesDigest.parse(newer) == .failure(.unsupportedSchema(2)))
    }

    @Test func garbageAndEngineErrorsAreNamed() {
        #expect(ChangesDocument.parse(Data("Usage: scoutctl".utf8)) == .failure(.notADocument))
        #expect(EngagementDocument.parse(Data(#"{"severity":"green"}"#.utf8)) == .failure(.notADocument))
        #expect(ChangesDigest.parse(Data(#"{"schema_version":1,"error":"vault directory missing"}"#.utf8))
                == .failure(.engine("vault directory missing")))
    }

    @Test func aBadRowIsSkippedNotFatal() throws {
        var root = try #require(JSONSerialization.jsonObject(with: Self.data("changes")) as? [String: Any])
        var rows = try #require(root["changes"] as? [[String: Any]])
        rows[0]["ts"] = "nope"
        root["changes"] = rows
        var items = try #require(root["needs_ok"] as? [[String: Any]])
        items[1].removeValue(forKey: "diff_hash")
        root["needs_ok"] = items
        let doc = try ChangesDocument.parse(JSONSerialization.data(withJSONObject: root)).get()
        #expect(doc.changes.count == 6)
        #expect(doc.needsOK.count == 1)
        #expect(doc.skippedRows == 2)
    }

    @Test func aBadEngagementDayIsSkipped() throws {
        let json = #"{"schema_version":1,"days":[{"date":"2026-11-01","feedback":1},{"feedback":2},{"date":"2026-11-03","noise":"x"}],"pending":{}}"#
        let doc = try EngagementDocument.parse(Data(json.utf8)).get()
        #expect(doc.days.map(\.date) == ["2026-11-01"])
        #expect(doc.skippedRows == 2)
        #expect(doc.pendingDMs == 0)
    }
}
