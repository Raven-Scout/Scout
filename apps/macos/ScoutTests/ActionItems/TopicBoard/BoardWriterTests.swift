import Testing
import Foundation
@testable import Scout

@Suite("Topic board — writes through scoutctl")
struct BoardWriterTests {
    private actor CommitLog: GitServiceProtocol {
        var commits: [(paths: [String], message: String)] = []
        func commitPaths(_ relPaths: [String], message: String) async throws {
            commits.append((relPaths, message))
        }
    }

    private let vault = URL(fileURLWithPath: "/tmp/vault")
    private let scoutctl = URL(fileURLWithPath: "/usr/local/bin/scoutctl")

    private func writer(runner: any ProcessRunner, git: CommitLog) -> BoardWriter {
        BoardWriter(
            scoutctl: scoutctl,
            actionItemsDirectory: vault.appendingPathComponent("action-items"),
            scoutDirectory: vault,
            runner: runner,
            git: git
        )
    }

    @Test func renameRunsScoutctlAndCommitsTheTopicsFile() async throws {
        let runner = RecordingRunner()
        let git = CommitLog()
        try await writer(runner: runner, git: git).submit(.renameTopic(tag: "STRM", name: "Reattach"))
        let calls = await runner.calls
        #expect(calls.map(\.arguments) == [[
            "action-items", "rename-topic", "STRM", "--name", "Reattach", "--data-dir", "/tmp/vault",
        ]])
        let commits = await git.commits
        #expect(commits.map(\.paths) == [["action-items/topics.md"]])
        #expect(commits.map(\.message) == ["action-items: rename topic #STRM"])
    }

    @Test func dismissCommitsTheDismissedFile() async throws {
        let runner = RecordingRunner()
        let git = CommitLog()
        try await writer(runner: runner, git: git).submit(.dismiss(key: "https://a.example/1"))
        #expect(await runner.calls.map(\.arguments) == [[
            "action-items", "dismiss", "--key", "https://a.example/1", "--data-dir", "/tmp/vault",
        ]])
        #expect(await git.commits.map(\.paths) == [["action-items/dismissed.md"]])
    }

    @Test func failedCommandThrowsAndCommitsNothing() async {
        let git = CommitLog()
        await #expect(throws: ActionItemsWriterError.self) {
            try await writer(runner: FailingRunner(exit: 1, stderr: "no topic"), git: git).submit(.renameTopic(tag: "NOPE", name: ""))
        }
        #expect(await git.commits.isEmpty)
    }

    @Test func clearingANameSaysSo() {
        #expect(BoardWriteOp.renameTopic(tag: "STRM", name: "").commitMessage == "action-items: clear topic name #STRM")
    }

    @Test func boardWritesNeedTheEngineFeature() async {
        let manifest = #"{"features": {"planning_v1": true, "topic_board_v1": true}, "schema_version": 1}"#
        let supported = RecordingRunner()
        supported.scripted = [ProcessResult(exitCode: 0, stdout: Data(manifest.utf8), stderr: Data())]
        let w = writer(runner: supported, git: CommitLog())
        #expect(await w.supportsBoardWrites())
        #expect(await w.supportsBoardWrites())
        #expect(await supported.calls.map(\.arguments) == [["manifest", "show"]])

        let old = RecordingRunner()
        old.scripted = [ProcessResult(exitCode: 0, stdout: Data(#"{"features": {"planning_v1": true}}"#.utf8), stderr: Data())]
        #expect(await writer(runner: old, git: CommitLog()).supportsBoardWrites() == false)

        #expect(await writer(runner: FailingRunner(exit: 2, stderr: "No such command"), git: CommitLog()).supportsBoardWrites() == false)
    }

    @Test func engineFeaturesKeepOnlyEnabledFlags() {
        #expect(EngineFeatures.decode(Data(#"{"features": {"a": true, "b": false}}"#.utf8)) == ["a"])
        #expect(EngineFeatures.decode(Data("not json".utf8)).isEmpty)
    }
}
