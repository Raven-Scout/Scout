import Testing
import Foundation
@testable import Scout

@Suite("GitService file history")
struct GitServiceFileHistoryTests {

    private let rel = "docs/wishlist/2026-04-01-example-item.md"
    private let RS = "\u{1E}", US = "\u{1F}"

    private func git(_ results: [ProcessResult]) -> (GitService, ScriptedRunner) {
        let runner = ScriptedRunner(scripted: results)
        return (GitService(repoURL: URL(fileURLWithPath: "/tmp/Scout"), runner: runner), runner)
    }

    private var threeRevisions: String {
        """
        \(RS)bbb222\(US)bbb\(US)1776000600\(US)app: mark Example item done

        diff --git a/\(rel) b/\(rel)
        index 1111111..2222222 100644
        --- a/\(rel)
        +++ b/\(rel)
        @@ -1,4 +1,4 @@
         ---
         title: "Example item"
        -status: open
        +status: done
        \(RS)mmm333\(US)mmm\(US)1775990000\(US)Merge branch 'side'
        \(RS)aaa111\(US)aaa\(US)1775900000\(US)dreaming [22:1x]: wishlist — filed the example item

        diff --git a/\(rel) b/\(rel)
        new file mode 100644
        --- /dev/null
        +++ b/\(rel)
        @@ -0,0 +1,3 @@
        +---
        +status: open
        +---

        """
    }

    @Test func fileHistoryParsesRevisionsNewestFirst() async throws {
        let (g, runner) = git([ProcessResult(exitCode: 0, stdout: Data(threeRevisions.utf8), stderr: Data())])

        let revs = try await g.fileHistory(relativePath: rel)

        #expect(revs.map(\.id) == ["bbb222", "mmm333", "aaa111"])
        #expect(revs[0].commit.subject == "app: mark Example item done")
        #expect(revs[0].commit.insertions == 1 && revs[0].commit.deletions == 1)
        #expect(revs[0].patch.contains("+status: done"))
        #expect(revs[1].patch.isEmpty)                       // merge: no patch for this file
        #expect(revs[2].commit.insertions == 3 && revs[2].commit.deletions == 0)
        #expect(revs[2].commit.timestamp == Date(timeIntervalSince1970: 1_775_900_000))

        let args = try #require(runner.calls.first?.arguments)
        #expect(args.contains("--follow") && args.contains("--patch") && args.contains("--no-color"))
        #expect(Array(args.suffix(2)) == ["--", rel])
    }

    @Test func recordSeparatorMidLineDoesNotSplitARevision() {
        let text = "\(RS)ccc\(US)ccc\(US)1776000000\(US)app: edit\n\n+odd \(RS) byte in content\n"
        let revs = GitService.parseFileHistory(text)
        #expect(revs.count == 1)
        #expect(revs[0].patch.contains("odd"))
    }

    @Test func fileHistoryThrowsOnGitError() async {
        let (g, _) = git([ProcessResult(exitCode: 128, stdout: Data(), stderr: Data("fatal".utf8))])
        await #expect(throws: GitServiceError.self) { _ = try await g.fileHistory(relativePath: rel) }
    }

    @Test func filesChangedListsNamesOnly() async throws {
        let out = "docs/wishlist/2026-04-01-example-item.md\nknowledge-base/example-topic.md\n\n"
        let (g, runner) = git([ProcessResult(exitCode: 0, stdout: Data(out.utf8), stderr: Data())])

        let files = try await g.filesChanged(inCommit: "aaa111")

        #expect(files == ["docs/wishlist/2026-04-01-example-item.md", "knowledge-base/example-topic.md"])
        let args = try #require(runner.calls.first?.arguments)
        #expect(args.contains("show") && args.contains("--name-only") && args.contains("--format="))
        #expect(args.last == "aaa111")
    }
}
