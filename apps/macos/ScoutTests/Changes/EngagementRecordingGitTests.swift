import Foundation
import Testing
@testable import Scout

/// A base git that records its commits and can be told to throw.
private final class FakeGit: GitServiceProtocol, @unchecked Sendable {
    struct Boom: Error {}
    private let lock = NSLock()
    private var _commits: [[String]] = []
    let fails: Bool
    init(fails: Bool = false) { self.fails = fails }
    var commits: [[String]] { lock.withLock { _commits } }
    func commitPaths(_ relPaths: [String], message: String) async throws {
        lock.withLock { _commits.append(relPaths) }
        if fails { throw Boom() }
    }
}

private final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var n = 0
    var count: Int { lock.withLock { n } }
    func bump() { lock.withLock { n += 1 } }
}

@MainActor
@Suite("EngagementRecordingGit")
struct EngagementRecordingGitTests {
    private static let exe = URL(fileURLWithPath: "/usr/bin/scoutctl")

    private func rig(recordExit: Int32 = 0) -> (RuleBasedRunner, ChangesDecisions, EngagementRecorder) {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["engagement", "record"], stderr: recordExit == 0 ? "" : "refused\n", exit: recordExit)
        let decisions = ChangesDecisions(runner: runner, scoutctl: { Self.exe })
        let recorder = EngagementRecorder()
        recorder.bind(decisions)
        return (runner, decisions, recorder)
    }

    @Test func aSuccessfulCommitRecordsOneAppEdit() async throws {
        let (runner, decisions, recorder) = rig()
        defer { withExtendedLifetime(decisions) {} }
        let base = FakeGit()
        let git = EngagementRecordingGit(base: base, record: { await recorder.record() })
        try await git.commitPaths(["a.md"], message: "m")
        #expect(base.commits == [["a.md"]])
        #expect(runner.calls.count == 1)
        #expect(runner.calls.last?.arguments == ["engagement", "record", "app-edit", "--surface", "app"])
    }

    @Test func aFailedCommitRecordsNothing() async {
        let counter = Counter()
        let git = EngagementRecordingGit(base: FakeGit(fails: true), record: { counter.bump() })
        await #expect(throws: FakeGit.Boom.self) { try await git.commitPaths(["a.md"], message: "m") }
        #expect(counter.count == 0)
    }

    @Test func aRefusedRecordNeverFailsTheWrite() async throws {
        let (runner, decisions, recorder) = rig(recordExit: 2)
        defer { withExtendedLifetime(decisions) {} }
        let base = FakeGit()
        let git = EngagementRecordingGit(base: base, record: { await recorder.record() })
        try await git.commitPaths(["a.md"], message: "m")   // does not throw
        #expect(base.commits.count == 1)
        #expect(runner.calls.count == 1)
    }

    @Test func anUnboundRecorderDoesNothing() async throws {
        let recorder = EngagementRecorder()
        let git = EngagementRecordingGit(base: FakeGit(), record: { await recorder.record() })
        try await git.commitPaths(["a.md"], message: "m")
    }

    @Test func aRecorderDoesNotKeepDecisionsAlive() async {
        let recorder = EngagementRecorder()
        weak var weakDecisions: ChangesDecisions?
        do {
            let d = ChangesDecisions(runner: RuleBasedRunner(), scoutctl: { nil })
            weakDecisions = d
            recorder.bind(d)
        }
        #expect(weakDecisions == nil)
        await recorder.record()   // no crash, nothing to do
    }
}
