import Testing
import Foundation
@testable import Scout

@Suite("ItemActivity")
struct ItemActivityTests {

    private let t0 = ISO8601DateFormatter().date(from: "2026-04-20T22:00:00Z")!

    private func rev(_ id: String, _ subject: String, at offset: TimeInterval = 0,
                     patch: String = "") -> FileRevision {
        FileRevision(commit: Commit(id: id, shortSHA: id, timestamp: t0.addingTimeInterval(offset),
                                    subject: subject, filesChanged: 1, insertions: 0, deletions: 0),
                     patch: patch)
    }

    // MARK: Sources

    @Test func labelsEachKindOfCommit() {
        let dreaming = Run.make(type: .dreaming, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        let s = { (subject: String) in
            ItemActivity.source(for: self.rev("x", subject, at: 300).commit, runs: [dreaming], now: self.t0)
        }
        #expect(s("dreaming [22:0x]: wishlist — filed the example item") == .run(dreaming, .dreaming))
        #expect(s("research [22:0x]: example findings") == .family(.research))   // no research run
        #expect(s("app: mark Example item done") == .app)
        #expect(s("scout: tidy the example folder") == .other)
        #expect(ActivitySource.app.label == "You")
        #expect(ActivitySource.other.label == "Other")
        #expect(ActivitySource.run(dreaming, .dreaming).label == "Dreaming")
    }

    // MARK: Resolving revision

    @Test func resolvingIsTheCommitThatAddedTheCurrentStatus_notTheNewest() {
        let revisions = [
            rev("c3", "dreaming [22:3x]: appended a note", patch: "+- a later note"),
            rev("c2", "app: mark Example item done", patch: "-status: open\n+status: done"),
            rev("c1", "dreaming [22:1x]: filed it", patch: "+---\n+status: open\n+---"),
        ]
        let entries = ItemActivity.entries(revisions: revisions, runs: [], status: .done, now: t0)
        #expect(entries.map(\.isResolving) == [false, true, false])
        #expect(ItemActivity.outcome(entries: entries, status: .done) == .resolved(entries[1]))
    }

    @Test func itemCreatedAlreadyResolvedResolvesAtCreation() {
        let entries = ItemActivity.entries(
            revisions: [rev("c1", "research: example findings", patch: "+---\n+status: done\n+---")],
            runs: [], status: .done, now: t0)
        #expect(entries[0].isResolving)
    }

    @Test func matchesTheCurrentTerminalValueOnly_andToleratesQuotes() {
        #expect(ItemActivity.addsStatus("+status: \"dropped\"", to: .dropped))
        #expect(!ItemActivity.addsStatus("+status: done", to: .dropped))
        #expect(!ItemActivity.addsStatus("-status: dropped", to: .dropped))
        #expect(!ItemActivity.addsStatus("+status: open", to: .open))   // active → never "resolving"
    }

    @Test func resolvedWithoutACommitIsUncommitted() {
        let entries = ItemActivity.entries(
            revisions: [rev("c1", "app: add wishlist item Example item", patch: "+status: open")],
            runs: [], status: .done, now: t0)
        #expect(entries.allSatisfy { !$0.isResolving })
        #expect(ItemActivity.outcome(entries: entries, status: .done) == .resolvedUncommitted)
    }

    @Test func activeItemsHaveNoOutcome() {
        let entries = ItemActivity.entries(
            revisions: [rev("c1", "app: start Example item", patch: "-status: open\n+status: in-progress")],
            runs: [], status: .inProgress, now: t0)
        #expect(ItemActivity.outcome(entries: entries, status: .inProgress) == nil)
    }

    @Test func outcomeSummaryWording() {
        let d: (Date) -> String = { _ in "Apr 20" }
        let dreaming = Run.make(type: .dreaming, startedAt: t0)
        func entry(_ s: ActivitySource) -> ItemActivityEntry {
            ItemActivityEntry(revision: rev("c", "x"), source: s, isResolving: true)
        }
        #expect(ItemOutcome.resolved(entry(.run(dreaming, .dreaming))).summary(date: d) == "Resolved by Dreaming · Apr 20")
        #expect(ItemOutcome.resolved(entry(.family(.research))).summary(date: d) == "Resolved by Research · Apr 20")
        #expect(ItemOutcome.resolved(entry(.app)).summary(date: d) == "Resolved by you · Apr 20")
        #expect(ItemOutcome.resolved(entry(.other)).summary(date: d) == "Resolved · Apr 20")
        #expect(ItemOutcome.resolvedUncommitted.summary(date: d) == "Resolved — not committed yet")
    }

    // MARK: Paths

    @Test func repoRelativePathIsNilOutsideTheRepo() {
        let repo = URL(fileURLWithPath: "/tmp/Scout")
        #expect(ItemActivity.repoRelativePath(
            of: URL(fileURLWithPath: "/tmp/Scout/docs/wishlist/a.md"), repo: repo) == "docs/wishlist/a.md")
        #expect(ItemActivity.repoRelativePath(
            of: URL(fileURLWithPath: "/tmp/Elsewhere/a.md"), repo: repo) == nil)
        #expect(ItemActivity.repoRelativePath(
            of: URL(fileURLWithPath: "/tmp/ScoutOther/a.md"), repo: repo) == nil)
    }
}
