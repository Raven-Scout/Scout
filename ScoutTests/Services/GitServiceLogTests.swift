import Foundation
import Testing
@testable import Scout

/// Covers `GitService`'s read paths — `git log` parsing (record-separator
/// format + `--shortstat` continuation lines), the subject-prefix filter, and
/// `diff`. The write paths live in `GitServiceCommitPathsTests` /
/// `GitServiceCommitAllTests`.
@Suite("GitService.log")
struct GitServiceLogTests {

    private let sep = "\u{1E}"

    private func logLine(
        sha: String, short: String, unixTime: Int, subject: String
    ) -> String {
        [sha, short, String(unixTime), subject].joined(separator: sep)
    }

    private func service(stdout: String, exitCode: Int32 = 0) -> (GitService, ScriptedRunner) {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: exitCode, stdout: Data(stdout.utf8), stderr: Data()),
        ])
        return (GitService(repoURL: URL(fileURLWithPath: "/tmp/repo"), runner: runner), runner)
    }

    // MARK: - parseShortStat

    @Test("shortstat captures files / insertions / deletions")
    func parseShortStat_fullLine() {
        let (files, ins, del) = GitService.parseShortStat(
            " 3 files changed, 42 insertions(+), 7 deletions(-)")
        #expect(files == 3)
        #expect(ins == 42)
        #expect(del == 7)
    }

    @Test("shortstat handles singular forms and missing segments")
    func parseShortStat_singularAndPartial() {
        #expect(GitService.parseShortStat(" 1 file changed, 1 insertion(+)") == (1, 1, 0))
        #expect(GitService.parseShortStat(" 1 file changed, 2 deletions(-)") == (1, 0, 2))
        #expect(GitService.parseShortStat(" 5 files changed") == (5, 0, 0))
    }

    @Test("an unrelated line yields all zeroes")
    func parseShortStat_noMatches() {
        #expect(GitService.parseShortStat("nothing numeric here") == (0, 0, 0))
        #expect(GitService.parseShortStat("") == (0, 0, 0))
    }

    // MARK: - commits(between:and:matchingPrefix:)

    @Test("parses a commit with its shortstat continuation line")
    func commits_parsesRecordPlusShortstat() async throws {
        let out = """
        \(logLine(sha: "abc123def", short: "abc123d", unixTime: 1_781_600_000, subject: "scout: briefing"))
         2 files changed, 10 insertions(+), 3 deletions(-)

        """
        let (git, runner) = service(stdout: out)
        let commits = try await git.commits(
            between: Date(timeIntervalSince1970: 0),
            and: Date(timeIntervalSince1970: 2_000_000_000),
            matchingPrefix: "")

        #expect(commits.count == 1)
        let c = try #require(commits.first)
        #expect(c.id == "abc123def")
        #expect(c.shortSHA == "abc123d")
        #expect(c.subject == "scout: briefing")
        #expect(c.timestamp == Date(timeIntervalSince1970: 1_781_600_000))
        #expect(c.filesChanged == 2)
        #expect(c.insertions == 10)
        #expect(c.deletions == 3)

        // The command is a scoped `git log` with the record-separator format.
        let call = try #require(runner.calls.first)
        #expect(call.arguments.contains("log"))
        #expect(call.arguments.contains("--shortstat"))
        #expect(call.arguments.contains("-C"))
        #expect(call.arguments.contains("/tmp/repo"))
        #expect(call.arguments.contains { $0.hasPrefix("--since=") })
        #expect(call.arguments.contains { $0.hasPrefix("--until=") })
    }

    @Test("a commit with no shortstat line reports zero counts")
    func commits_withoutShortstat() async throws {
        let out = logLine(sha: "a", short: "a", unixTime: 100, subject: "empty commit") + "\n"
        let (git, _) = service(stdout: out)
        let commits = try await git.commits(
            between: .distantPast, and: .distantFuture, matchingPrefix: "")
        #expect(commits.count == 1)
        #expect(commits[0].filesChanged == 0)
        #expect(commits[0].insertions == 0)
        #expect(commits[0].deletions == 0)
    }

    @Test("prefix filter keeps only matching subjects")
    func commits_filtersBySubjectPrefix() async throws {
        let out = [
            logLine(sha: "1", short: "1", unixTime: 100, subject: "scout: briefing"),
            " 1 file changed, 1 insertion(+)",
            logLine(sha: "2", short: "2", unixTime: 200, subject: "app: edit note"),
            " 1 file changed, 2 insertions(+)",
            logLine(sha: "3", short: "3", unixTime: 300, subject: "scout: dreaming"),
        ].joined(separator: "\n")

        let (git, _) = service(stdout: out)
        let matched = try await git.commits(
            between: .distantPast, and: .distantFuture, matchingPrefix: "scout: ")
        #expect(matched.map(\.id) == ["1", "3"])
    }

    @Test("an empty prefix returns every commit in the window")
    func commits_emptyPrefixReturnsAll() async throws {
        let out = [
            logLine(sha: "1", short: "1", unixTime: 100, subject: "scout: briefing"),
            logLine(sha: "2", short: "2", unixTime: 200, subject: "app: edit note"),
        ].joined(separator: "\n")
        let (git, _) = service(stdout: out)
        let all = try await git.commits(
            between: .distantPast, and: .distantFuture, matchingPrefix: "")
        #expect(all.map(\.id) == ["1", "2"])
    }

    @Test("records with the wrong field count are skipped")
    func commits_skipsMalformedRecords() async throws {
        let out = [
            "sha\(sep)short\(sep)100",                   // only 3 fields
            logLine(sha: "ok", short: "ok", unixTime: 200, subject: "good"),
            "a line with no separator at all",
        ].joined(separator: "\n")
        let (git, _) = service(stdout: out)
        let commits = try await git.commits(
            between: .distantPast, and: .distantFuture, matchingPrefix: "")
        #expect(commits.map(\.id) == ["ok"])
    }

    @Test("a non-numeric timestamp falls back to the epoch")
    func commits_nonNumericTimestampFallsBackToEpoch() async throws {
        let out = ["sha", "short", "not-a-number", "subject"].joined(separator: sep)
        let (git, _) = service(stdout: out)
        let commits = try await git.commits(
            between: .distantPast, and: .distantFuture, matchingPrefix: "")
        #expect(commits.first?.timestamp == Date(timeIntervalSince1970: 0))
    }

    @Test("empty output yields no commits")
    func commits_emptyOutput() async throws {
        let (git, _) = service(stdout: "")
        let commits = try await git.commits(
            between: .distantPast, and: .distantFuture, matchingPrefix: "")
        #expect(commits.isEmpty)
    }

    @Test("a non-zero git exit surfaces as gitExitNonZero")
    func commits_nonZeroExitThrows() async throws {
        let (git, _) = service(stdout: "", exitCode: 128)
        await #expect(throws: GitServiceError.gitExitNonZero(128)) {
            _ = try await git.commits(
                between: .distantPast, and: .distantFuture, matchingPrefix: "")
        }
    }

    // MARK: - diff

    @Test("diff returns stdout verbatim and asks for the A..B range")
    func diff_returnsStdoutForRange() async throws {
        let patch = "diff --git a/x b/x\n+added\n"
        let (git, runner) = service(stdout: patch)
        let out = try await git.diff(from: "aaa", to: "bbb")
        #expect(out == patch)
        let call = try #require(runner.calls.first)
        #expect(call.arguments.contains("diff"))
        #expect(call.arguments.contains("aaa..bbb"))
    }

    @Test("diff of non-UTF8 output degrades to an empty string")
    func diff_nonUTF8OutputIsEmpty() async throws {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data([0xFF, 0xFE, 0xFD]), stderr: Data()),
        ])
        let git = GitService(repoURL: URL(fileURLWithPath: "/tmp/repo"), runner: runner)
        #expect(try await git.diff(from: "a", to: "b") == "")
    }

    // MARK: - index-lock classification

    @Test("index.lock contention is recognised from stderr, zero exit never is")
    func isIndexLockContention_classification() {
        let lock = ProcessResult(
            exitCode: 128, stdout: Data(),
            stderr: Data("fatal: Unable to create '/r/.git/index.lock': File exists.".utf8))
        #expect(GitService.isIndexLockContention(lock))

        let another = ProcessResult(
            exitCode: 1, stdout: Data(),
            stderr: Data("Another git process seems to be running".utf8))
        #expect(GitService.isIndexLockContention(another))

        let unrelated = ProcessResult(
            exitCode: 1, stdout: Data(), stderr: Data("pre-commit hook failed".utf8))
        #expect(!GitService.isIndexLockContention(unrelated))

        // A success is never contention, whatever the stderr says.
        let success = ProcessResult(
            exitCode: 0, stdout: Data(), stderr: Data("index.lock".utf8))
        #expect(!GitService.isIndexLockContention(success))
    }
}
