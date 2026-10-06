import Testing
import Foundation
@testable import Scout

@MainActor
@Suite("PerFileItemActivityModel")
struct PerFileItemActivityModelTests {

    private let repo = URL(fileURLWithPath: "/tmp/Scout")

    private func item(at path: String, status: ItemStatus = .open) -> PerFileItem {
        PerFileItem(fileURL: URL(fileURLWithPath: path), date: "2026-04-01",
                    title: "Example item", status: status, priority: .medium,
                    source: nil, area: nil, bodyMarkdown: "")
    }

    private func model(_ results: [ProcessResult]) -> (PerFileItemActivityModel, ScriptedRunner) {
        let runner = ScriptedRunner(scripted: results)
        return (PerFileItemActivityModel(git: GitService(repoURL: repo, runner: runner), repoURL: repo), runner)
    }

    @Test func loadPublishesRevisions() async throws {
        let out = "\u{1E}aaa\u{1F}aaa\u{1F}1776000000\u{1F}app: add wishlist item Example item\n\n+status: open\n"
        let (m, runner) = model([ProcessResult(exitCode: 0, stdout: Data(out.utf8), stderr: Data())])

        await m.load(item(at: "/tmp/Scout/docs/wishlist/2026-04-01-example-item.md"))

        guard case .loaded(let revs) = m.state else { Issue.record("got \(m.state)"); return }
        #expect(revs.map(\.id) == ["aaa"])
        #expect(runner.calls.first?.arguments.last == "docs/wishlist/2026-04-01-example-item.md")
    }

    @Test func gitErrorIsSurfaced() async {
        let (m, _) = model([ProcessResult(exitCode: 128, stdout: Data(), stderr: Data())])
        await m.load(item(at: "/tmp/Scout/docs/wishlist/x.md"))
        if case .failed = m.state {} else { Issue.record("expected .failed, got \(m.state)") }
    }

    @Test func outsideTheRepoIsUnavailableAndRunsNoGit() async {
        let (m, runner) = model([])
        await m.load(item(at: "/tmp/Elsewhere/x.md"))
        if case .unavailable = m.state {} else { Issue.record("expected .unavailable, got \(m.state)") }
        #expect(runner.calls.isEmpty)
    }

    @Test func otherFilesExcludeTheItemAndLoadOnce() async {
        let out = "docs/wishlist/x.md\nknowledge-base/example-topic.md\n"
        let hist = "\u{1E}aaa\u{1F}aaa\u{1F}1776000000\u{1F}research: example findings\n"
        let (m, runner) = model([
            ProcessResult(exitCode: 0, stdout: Data(hist.utf8), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(out.utf8), stderr: Data()),
        ])
        await m.load(item(at: "/tmp/Scout/docs/wishlist/x.md"))
        guard case .loaded(let revs) = m.state else { Issue.record("not loaded"); return }

        await m.loadOtherFiles(for: revs[0].commit)
        await m.loadOtherFiles(for: revs[0].commit)

        #expect(m.otherFiles["aaa"] == .loaded(["knowledge-base/example-topic.md"]))
        #expect(runner.calls.count == 2)   // history + one show
    }
}
