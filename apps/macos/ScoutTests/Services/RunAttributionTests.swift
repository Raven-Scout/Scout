import Testing
import Foundation
@testable import Scout

@Suite("Run attribution — commit family, window, reverse link")
struct RunAttributionTests {

    private let t0 = ISO8601DateFormatter().date(from: "2026-04-20T22:00:00Z")!

    private func commit(_ subject: String, at offset: TimeInterval) -> Commit {
        Commit(id: "\(subject)-\(offset)", shortSHA: "abc1234",
               timestamp: t0.addingTimeInterval(offset), subject: subject,
               filesChanged: 1, insertions: 1, deletions: 0)
    }

    // MARK: CommitFamily

    @Test func familyReadsTheSubjectToken() {
        #expect(CommitFamily.of(subject: "weekend briefing [2026-04-18]: nothing new on the example board") == .weekendBriefing)
        #expect(CommitFamily.of(subject: "briefing [2026-04-20]: the day ahead") == .briefing)
        #expect(CommitFamily.of(subject: "consolidation [13:1x]: example sweep") == .consolidation)
        #expect(CommitFamily.of(subject: "dreaming [22:1x]: wishlist — filed the example item") == .dreaming)
        #expect(CommitFamily.of(subject: "research: example findings") == .research)
    }

    @Test func familyNeedsADelimiterAfterTheToken() {
        #expect(CommitFamily.of(subject: "researcher notes") == nil)
        #expect(CommitFamily.of(subject: "app: mark Example item done") == nil)
        #expect(CommitFamily.of(subject: "scout: tidy the example folder") == nil)
        #expect(CommitFamily.of(subject: "") == nil)
    }

    @Test func familiesMapToRunners() {
        #expect(CommitFamily.weekendBriefing.runnerScript == "run-scout.sh")
        #expect(CommitFamily.briefing.runnerScript == "run-scout.sh")
        #expect(CommitFamily.consolidation.runnerScript == "run-scout.sh")
        #expect(CommitFamily.dreaming.runnerScript == "run-dreaming.sh")
        #expect(CommitFamily.research.runnerScript == "run-research.sh")
    }

    // MARK: commitWindow / claims

    @Test func headerStartWinsOverFilenameStart() {
        let run = Run.make(type: .research, startedAt: t0.addingTimeInterval(-6 * 3600),
                           endedAt: t0.addingTimeInterval(600), headerStartedAt: t0)
        let w = run.commitWindow(now: t0.addingTimeInterval(86_400))
        #expect(w.lowerBound == t0.addingTimeInterval(-30))
        #expect(w.upperBound == t0.addingTimeInterval(600 + 300))
    }

    @Test func unendedNonRunningRunIsCappedAtItsOrphanCutoff() {
        let run = Run.make(type: .dreaming, startedAt: t0, endedAt: nil, status: .orphaned)
        let w = run.commitWindow(now: t0.addingTimeInterval(10 * 3600))
        #expect(w.upperBound == t0.addingTimeInterval(2 * 3600 + 300))
    }

    @Test func runningRunExtendsToNow() {
        let run = Run.make(type: .dreaming, startedAt: t0, endedAt: nil, status: .running)
        let now = t0.addingTimeInterval(600)
        #expect(run.commitWindow(now: now).upperBound == now.addingTimeInterval(300))
    }

    @Test func windowIsNeverInverted() {
        // No header + an absolute end that predates the drifted filename start.
        let run = Run.make(type: .research, startedAt: t0, endedAt: t0.addingTimeInterval(-3600))
        let w = run.commitWindow(now: t0)
        #expect(w.lowerBound <= w.upperBound)
        #expect(w.upperBound == t0.addingTimeInterval(270))
    }

    @Test func scoutRunnerClaimsAllThreeOfItsFamilies() {
        let run = Run.make(type: .consolidation, startedAt: t0)
        #expect(run.claims(commit("briefing [2026-04-20]: the day ahead", at: 0)))
        #expect(run.claims(commit("weekend briefing [2026-04-18]: nothing new on the example board", at: 0)))
        #expect(run.claims(commit("consolidation [13:1x]: example sweep", at: 0)))
        #expect(!run.claims(commit("dreaming [22:1x]: wishlist — x", at: 0)))
        #expect(!run.claims(commit("app: mark Example item done", at: 0)))
    }

    // MARK: CommitRunLinker

    @Test func linksAClaimedCommitInsideTheWindow() {
        let run = Run.make(type: .dreaming, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        let c = commit("dreaming [22:0x]: wishlist — filed the example item", at: 300)
        #expect(CommitRunLinker.run(for: c, in: [run], now: t0)?.id == run.id)
    }

    @Test func appCommitsNeverLink() {
        let run = Run.make(type: .dreaming, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        #expect(CommitRunLinker.run(for: commit("app: mark Example item done", at: 300),
                                    in: [run], now: t0) == nil)
    }

    @Test func outOfWindowDoesNotLink() {
        let run = Run.make(type: .dreaming, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        #expect(CommitRunLinker.run(for: commit("dreaming [23:5x]: late", at: 7200),
                                    in: [run], now: t0) == nil)
    }

    @Test func familyRunnerMismatchDoesNotLink() {
        let run = Run.make(type: .research, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        #expect(CommitRunLinker.run(for: commit("dreaming [22:0x]: x", at: 300),
                                    in: [run], now: t0) == nil)
    }

    @Test func skippedRunsNeverClaimCommits() {
        // A concurrency/budget skip never starts Claude, writes no finish
        // marker, and starts after the run it was skipped by — without this
        // guard it would win the latest-start tie-break.
        let real = Run.make(type: .research, startedAt: t0, endedAt: t0.addingTimeInterval(3600))
        let skippedConcurrency = Run.make(type: .research, startedAt: t0.addingTimeInterval(60),
                                          endedAt: nil, status: .skippedConcurrency)
        let skippedBudget = Run.make(type: .research, startedAt: t0.addingTimeInterval(90),
                                     endedAt: nil, status: .skippedBudget)
        let c = commit("research [22:0x]: example findings", at: 300)
        #expect(!skippedConcurrency.claims(c))
        #expect(!skippedBudget.claims(c))
        #expect(CommitRunLinker.run(for: c, in: [real, skippedConcurrency, skippedBudget], now: t0)?.id == real.id)
    }

    @Test func overlappingRunsPickTheLatestStart() {
        let older = Run.make(type: .research, startedAt: t0, endedAt: t0.addingTimeInterval(3600))
        let newer = Run.make(type: .research, startedAt: t0.addingTimeInterval(100),
                             endedAt: t0.addingTimeInterval(3600))
        let c = commit("research [22:0x]: example findings", at: 200)
        #expect(CommitRunLinker.run(for: c, in: [older, newer], now: t0)?.id == newer.id)
    }
}
