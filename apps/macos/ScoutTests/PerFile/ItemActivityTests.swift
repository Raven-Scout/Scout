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

    @Test func itemCreatedAlreadyResolvedSaysSoInsteadOfClaimingAResolutionDate() {
        // A bulk import (or a run that files an item already done) creates the
        // file with a terminal status: its date is when the file appeared, not
        // when the work was resolved.
        let d: (Date) -> String = { _ in "Apr 20" }
        let creation = "diff --git a/x.md b/x.md\nnew file mode 100644\n--- /dev/null\n+++ b/x.md\n@@ -0,0 +1,3 @@\n+---\n+status: done\n+---"
        let created = ItemActivityEntry(revision: rev("c1", "x", patch: creation), source: .other, isResolving: true)
        let createdByRun = ItemActivityEntry(revision: rev("c1", "x", patch: creation),
                                             source: .family(.research), isResolving: true)
        #expect(created.revision.createsFile)
        #expect(ItemOutcome.resolved(created).summary(date: d) == "Created as resolved · Apr 20")
        #expect(ItemOutcome.resolved(createdByRun).summary(date: d) == "Created as resolved by Research · Apr 20")
        #expect(!rev("c2", "x", patch: "-status: open\n+status: done").createsFile)
    }

    // MARK: Reload key

    @Test func reloadKeyChangesWhenACommitCanHaveLanded() {
        // A commit alone never changes the item on disk, so the pane needs
        // other signals: the app finishing a write, or a run finishing.
        let item = PerFileItem(fileURL: URL(fileURLWithPath: "/tmp/Scout/docs/wishlist/x.md"),
                               date: "2026-04-01", title: "Example item", status: .open,
                               priority: .medium, source: nil, area: nil, bodyMarkdown: "")
        let running = Run.make(type: .dreaming, startedAt: t0, endedAt: nil, status: .running)
        let older = Run.make(type: .research, startedAt: t0.addingTimeInterval(-7200),
                             endedAt: t0.addingTimeInterval(-3600))
        let base = HistoryReloadKey(item: item, writeToken: 0, runs: [running, older])

        #expect(base == HistoryReloadKey(item: item, writeToken: 0, runs: [running, older]))
        #expect(base != HistoryReloadKey(item: item, writeToken: 1, runs: [running, older]))
        let finished = Run.make(type: .dreaming, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        #expect(base != HistoryReloadKey(item: item, writeToken: 0, runs: [finished, older]))
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
