// ScoutTests/PerFile/PerFileItemWriterTests.swift
import Foundation
import Testing
@testable import Scout

@Suite("PerFileItemWriter pure helpers")
struct PerFileItemWriterPureTests {
    @Test func slugifyBasic() {
        #expect(PerFileItemWriter.slugify("Upgrade the Graph System!") == "upgrade-the-graph-system")
        #expect(PerFileItemWriter.slugify("G6 · CEE conference entities") == "g6-cee-conference-entities")
    }
    @Test func slugifyTruncatesToEightWords() {
        #expect(PerFileItemWriter.slugify("one two three four five six seven eight nine ten") == "one-two-three-four-five-six-seven-eight")
    }
    @Test func renderEmitsQuotedFrontmatterAndStrippableBody() throws {
        let text = PerFileItemWriter.renderItemFile(title: "Build a config: store", status: .open,
            priority: .high, date: "2026-06-19", source: "Jordan DM", area: nil, body: "The body.")
        #expect(text.hasPrefix("---\n"))
        #expect(text.contains("title: \"Build a config: store\""))   // colon -> quoted
        #expect(text.contains("status: open"))
        #expect(text.contains("priority: high"))
        #expect(text.contains("date: 2026-06-19"))
        #expect(text.contains("source: \"Jordan DM\""))
        #expect(!text.contains("area:"))
        #expect(text.contains("\n# Build a config: store\n"))
        // round-trips through the parser
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: URL(fileURLWithPath: "/tmp/x.md")))
        #expect(item.title == "Build a config: store" && item.status == .open && item.priority == .high && item.source == "Jordan DM")
    }
    @Test func renderResearchAreaNoSource() {
        let text = PerFileItemWriter.renderItemFile(title: "T", status: .open, priority: .urgent,
            date: "2026-06-19", source: nil, area: "kg", body: "b")
        #expect(text.contains("area: \"kg\""))
        #expect(!text.contains("source:"))
    }
    @Test func rewriteFieldReplacesStatusPreservesRest() throws {
        let text = "---\ntitle: X\nstatus: open\npriority: high\n---\n\n# X\nbody"
        let updated = try PerFileItemWriter.rewriteFrontmatterField(text: text, key: "status", value: "done", file: "x.md")
        #expect(updated.contains("status: done"))
        #expect(updated.contains("priority: high"))
        #expect(updated.contains("# X\nbody"))
    }

    @Test func rewriteFieldReplacesPriorityPreservesRest() throws {
        let text = "---\ntitle: X\nstatus: open\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody"
        let updated = try PerFileItemWriter.rewriteFrontmatterField(text: text, key: "priority", value: "urgent", file: "x.md")
        #expect(updated.contains("priority: urgent"))
        #expect(updated.contains("status: open"))         // untouched
        #expect(updated.contains("date: 2026-06-10"))     // untouched
        #expect(updated.contains("# X\nbody"))
    }

    @Test func rewriteFieldThrowsWhenFieldMissing() throws {
        let text = "---\ntitle: X\nstatus: open\n---\n\n# X\nbody"  // no priority:
        #expect(throws: PerFileItemWriterError.fieldNotFound(field: "priority", file: "x.md")) {
            _ = try PerFileItemWriter.rewriteFrontmatterField(text: text, key: "priority", value: "low", file: "x.md")
        }
    }
}

@Suite("PerFileItemWriter end-to-end (file + git)")
struct PerFileItemWriterE2ETests {
    private static func fixedDate() -> Date {
        var c = DateComponents(); c.year = 2026; c.month = 6; c.day = 19; c.hour = 12
        c.timeZone = TimeZone(identifier: "UTC")
        return Calendar(identifier: .gregorian).date(from: c)!
    }
    private func makeVault() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("perfile-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir.appendingPathComponent("docs/wishlist"), withIntermediateDirectories: true)
        return dir
    }
    private func okRunner() -> ScriptedRunner {  // rev-parse(0) add(0) diff(1=dirty) commit(0)
        ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
    }

    @Test func addItemWritesFileAndCommitsScoped() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("docs/wishlist")
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        let url = try await writer.addItem(title: "Alpha thing", priority: .high, body: "do alpha",
                                           source: "Jordan DM", area: nil, in: dir, noun: "wishlist item")
        #expect(url.lastPathComponent == "2026-06-19-alpha-thing.md")
        let written = try String(contentsOf: url, encoding: .utf8)
        #expect(written.contains("status: open") && written.contains("priority: high") && written.contains("source: \"Jordan DM\""))
        let commit = try #require(runner.calls.last)
        #expect(commit.arguments.contains("commit"))
        #expect(commit.arguments.contains("app: add wishlist item Alpha thing"))
        #expect(commit.arguments.contains("docs/wishlist/2026-06-19-alpha-thing.md"))
    }

    @Test func addItemDisambiguatesFilenameCollision() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("docs/wishlist")
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: nil, now: { Self.fixedDate() })
        let u1 = try await writer.addItem(title: "Same", priority: .medium, body: "a", source: nil, area: nil, in: dir, noun: "wishlist item")
        let u2 = try await writer.addItem(title: "Same", priority: .medium, body: "b", source: nil, area: nil, in: dir, noun: "wishlist item")
        #expect(u1.lastPathComponent == "2026-06-19-same.md")
        #expect(u2.lastPathComponent == "2026-06-19-same-2.md")
    }

    @Test func emptyTitleThrowsAndDoesNotCommit() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("docs/wishlist")
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        await #expect(throws: PerFileItemWriterError.emptyTitle) {
            _ = try await writer.addItem(title: "   ", priority: .medium, body: "x", source: nil, area: nil, in: dir, noun: "wishlist item")
        }
        #expect(runner.calls.isEmpty)
    }

    @Test func resolveFlipsStatusAndCommits() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("docs/wishlist")
        let fileURL = dir.appendingPathComponent("2026-06-10-x.md")
        try "---\ntitle: X\nstatus: open\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody".write(to: fileURL, atomically: true, encoding: .utf8)
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        try await writer.resolve(.done, fileURL: fileURL, label: "X")
        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written.contains("status: done"))
        let commit = try #require(runner.calls.last)
        #expect(commit.arguments.contains("app: mark X done"))
        #expect(commit.arguments.contains("docs/wishlist/2026-06-10-x.md"))
    }

    @Test func setPriorityRewritesFieldAndCommitsScoped() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("docs/wishlist")
        let fileURL = dir.appendingPathComponent("2026-06-10-x.md")
        try "---\ntitle: X\nstatus: open\npriority: medium\ndate: 2026-06-10\n---\n\n# X\nbody"
            .write(to: fileURL, atomically: true, encoding: .utf8)
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        try await writer.setPriority(.urgent, fileURL: fileURL, label: "X")
        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written.contains("priority: urgent"))
        #expect(written.contains("status: open"))
        let commit = try #require(runner.calls.last)
        #expect(commit.arguments.contains("commit"))
        #expect(commit.arguments.contains("app: set X priority to urgent"))
        #expect(commit.arguments.contains("docs/wishlist/2026-06-10-x.md"))
    }
}
