// ScoutTests/PerFile/PerFileItemWriterTests.swift
import Foundation
import Testing
@testable import Scout

@Suite("PerFileItemWriter pure helpers")
struct PerFileItemWriterPureTests {
    @Test func slugifyBasic() {
        #expect(PerFileItemWriter.slugify("Upgrade the Graph System!") == "upgrade-the-graph-system")
        #expect(PerFileItemWriter.slugify("X9 · DEMO conference entities") == "x9-demo-conference-entities")
    }
    @Test func slugifyTruncatesToEightWords() {
        #expect(PerFileItemWriter.slugify("one two three four five six seven eight nine ten") == "one-two-three-four-five-six-seven-eight")
    }
    @Test func renderEmitsQuotedFrontmatterAndStrippableBody() throws {
        let text = PerFileItemWriter.renderItemFile(title: "Build a config: store", status: .open,
            priority: .high, date: "2026-06-19", source: "Alex DM", area: nil, body: "The body.")
        #expect(text.hasPrefix("---\n"))
        #expect(text.contains("title: \"Build a config: store\""))   // colon -> quoted
        #expect(text.contains("status: open"))
        #expect(text.contains("priority: high"))
        #expect(text.contains("date: 2026-06-19"))
        #expect(text.contains("source: \"Alex DM\""))
        #expect(!text.contains("area:"))
        #expect(text.contains("\n# Build a config: store\n"))
        // round-trips through the parser
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: URL(fileURLWithPath: "/tmp/x.md")))
        #expect(item.title == "Build a config: store" && item.status == .open && item.priority == .high && item.source == "Alex DM")
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

    // MARK: trailing YAML comments on keyword fields

    /// The frontmatter line for `key` after rewriting it to `value`.
    private func rewrittenLine(_ line: String, key: String, value: String) throws -> String {
        let text = "---\ntitle: X\n\(line)\n---\n\n# X\nbody"
        let updated = try PerFileItemWriter.rewriteFrontmatterField(text: text, key: key, value: value, file: "x.md")
        #expect(updated.hasPrefix("---\ntitle: X\n") && updated.hasSuffix("\n---\n\n# X\nbody"))
        return try #require(updated.components(separatedBy: "\n").first { $0.lowercased().hasPrefix(key) })
    }

    @Test func rewriteKeepsUnquotedTrailingComment() throws {
        #expect(try rewrittenLine("status: open   # parked 2026-01-02 — waiting on vendor", key: "status", value: "in-progress")
                == "status: in-progress   # parked 2026-01-02 — waiting on vendor")
        #expect(try rewrittenLine("priority: low\t# bumped later", key: "priority", value: "urgent")
                == "priority: urgent\t# bumped later")
    }

    @Test func rewriteKeepsCommentAfterClosingQuote() throws {
        #expect(try rewrittenLine("status: \"open\"  # note", key: "status", value: "done") == "status: done  # note")
        #expect(try rewrittenLine("status: 'open' # note", key: "status", value: "dropped") == "status: dropped # note")
    }

    @Test func rewriteDropsHashInsideQuotes() throws {
        // A `#` inside the quotes is part of the value, not a comment.
        #expect(try rewrittenLine("status: \"open # not a comment\"", key: "status", value: "done") == "status: done")
    }

    @Test func rewriteKeepsHashNotAfterWhitespaceAsValue() throws {
        // `high#x` is one bare scalar; there's no comment to keep.
        #expect(try rewrittenLine("priority: high#x", key: "priority", value: "urgent") == "priority: urgent")
    }

    @Test func rewriteWithoutCommentIsUnchanged() throws {
        #expect(try rewrittenLine("status: open", key: "status", value: "done") == "status: done")
        #expect(try rewrittenLine("status: open   ", key: "status", value: "done") == "status: done")
        #expect(try rewrittenLine("status: \"open\"", key: "status", value: "done") == "status: done")
        #expect(try rewrittenLine("status: \"open", key: "status", value: "done") == "status: done")  // unterminated
    }

    @Test func rewriteKeepsCommentOnEmptyValue() throws {
        #expect(try rewrittenLine("status: # set me", key: "status", value: "open") == "status: open # set me")
    }

    @Test func rewriteSeparatesCommentGluedToClosingQuote() throws {
        // `"open"# note` must not become `done# note`, which reads back as one scalar.
        #expect(try rewrittenLine("status: \"open\"# note", key: "status", value: "done") == "status: done # note")
    }

    @Test func rewriteLeavesFreeTextHashAlone() throws {
        // Free-text fields keep `#` as content (`Pattern #197`), so there is no comment to carry over.
        #expect(try rewrittenLine("source: Pattern #197", key: "source", value: "Alex DM") == "source: Alex DM")
    }

    @Test func rewrittenCommentedStatusParsesAsNewValue() throws {
        let text = "---\ntitle: X\nstatus: done   # retired 2026-01-02 — expired\npriority: \"low\" # why\n---\n\n# X\nbody"
        var updated = try PerFileItemWriter.rewriteFrontmatterField(text: text, key: "status", value: "open", file: "x.md")
        updated = try PerFileItemWriter.rewriteFrontmatterField(text: updated, key: "priority", value: "high", file: "x.md")
        #expect(updated.contains("status: open   # retired 2026-01-02 — expired\npriority: high # why\n"))
        let item = try #require(PerFileItemParser.parseFile(contents: updated, fileURL: URL(fileURLWithPath: "/tmp/x.md")))
        #expect(item.status == .open && item.priority == .high)
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
        try FileManager.default.createDirectory(at: dir.appendingPathComponent("knowledge-base/research-queue"), withIntermediateDirectories: true)
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
        let dir = vault.appendingPathComponent("knowledge-base/research-queue")
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        let url = try await writer.addItem(title: "Alpha thing", priority: .high, body: "do alpha",
                                           source: "Alex DM", area: nil, in: dir, noun: "research topic")
        #expect(url.lastPathComponent == "2026-06-19-alpha-thing.md")
        let written = try String(contentsOf: url, encoding: .utf8)
        #expect(written.contains("status: open") && written.contains("priority: high") && written.contains("source: \"Alex DM\""))
        let commit = try #require(runner.calls.last)
        #expect(commit.arguments.contains("commit"))
        #expect(commit.arguments.contains("app: add research topic Alpha thing"))
        #expect(commit.arguments.contains("knowledge-base/research-queue/2026-06-19-alpha-thing.md"))
    }

    @Test func addItemDisambiguatesFilenameCollision() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("knowledge-base/research-queue")
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: nil, now: { Self.fixedDate() })
        let u1 = try await writer.addItem(title: "Same", priority: .medium, body: "a", source: nil, area: nil, in: dir, noun: "research topic")
        let u2 = try await writer.addItem(title: "Same", priority: .medium, body: "b", source: nil, area: nil, in: dir, noun: "research topic")
        #expect(u1.lastPathComponent == "2026-06-19-same.md")
        #expect(u2.lastPathComponent == "2026-06-19-same-2.md")
    }

    @Test func emptyTitleThrowsAndDoesNotCommit() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("knowledge-base/research-queue")
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        await #expect(throws: PerFileItemWriterError.emptyTitle) {
            _ = try await writer.addItem(title: "   ", priority: .medium, body: "x", source: nil, area: nil, in: dir, noun: "research topic")
        }
        #expect(runner.calls.isEmpty)
    }

    @Test func resolveFlipsStatusAndCommits() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("knowledge-base/research-queue")
        let fileURL = dir.appendingPathComponent("2026-06-10-x.md")
        try "---\ntitle: X\nstatus: open\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody".write(to: fileURL, atomically: true, encoding: .utf8)
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        try await writer.resolve(.done, fileURL: fileURL, label: "X")
        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written.contains("status: done"))
        let commit = try #require(runner.calls.last)
        #expect(commit.arguments.contains("app: mark X done"))
        #expect(commit.arguments.contains("knowledge-base/research-queue/2026-06-10-x.md"))
    }

    @Test func setPriorityRewritesFieldAndCommitsScoped() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("knowledge-base/research-queue")
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
        #expect(commit.arguments.contains("knowledge-base/research-queue/2026-06-10-x.md"))
    }

    @Test func setStatusStartFlipsOpenToInProgress() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let fileURL = vault.appendingPathComponent("knowledge-base/research-queue/2026-06-10-x.md")
        try "---\ntitle: X\nstatus: open\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody"
            .write(to: fileURL, atomically: true, encoding: .utf8)
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        try await writer.setStatus(.inProgress, fileURL: fileURL, label: "X")
        #expect(try String(contentsOf: fileURL, encoding: .utf8).contains("status: in-progress"))
        #expect(try #require(runner.calls.last).arguments.contains("app: start X"))
    }

    @Test func setStatusReopenFlipsDroppedToOpen() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let fileURL = vault.appendingPathComponent("knowledge-base/research-queue/2026-06-10-x.md")
        try "---\ntitle: X\nstatus: dropped\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody"
            .write(to: fileURL, atomically: true, encoding: .utf8)
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        try await writer.setStatus(.open, fileURL: fileURL, label: "X")
        #expect(try String(contentsOf: fileURL, encoding: .utf8).contains("status: open"))
        #expect(try #require(runner.calls.last).arguments.contains("app: reopen X"))
    }

    @Test func setStatusKeepsInlineComment() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let fileURL = vault.appendingPathComponent("knowledge-base/research-queue/2026-06-10-x.md")
        try "---\ntitle: X\nstatus: done   # retired 2026-06-12 — expires reached\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody"
            .write(to: fileURL, atomically: true, encoding: .utf8)
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: okRunner()), now: { Self.fixedDate() })
        try await writer.setStatus(.open, fileURL: fileURL, label: "X")
        #expect(try String(contentsOf: fileURL, encoding: .utf8)
                == "---\ntitle: X\nstatus: open   # retired 2026-06-12 — expires reached\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody")
    }

    @Test func setPriorityKeepsCommentAfterQuotedValue() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let fileURL = vault.appendingPathComponent("knowledge-base/research-queue/2026-06-10-x.md")
        try "---\ntitle: X\nstatus: open\npriority: \"medium\"  # lowered after review\ndate: 2026-06-10\n---\n\n# X\nbody"
            .write(to: fileURL, atomically: true, encoding: .utf8)
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: okRunner()), now: { Self.fixedDate() })
        try await writer.setPriority(.urgent, fileURL: fileURL, label: "X")
        #expect(try String(contentsOf: fileURL, encoding: .utf8)
                == "---\ntitle: X\nstatus: open\npriority: urgent  # lowered after review\ndate: 2026-06-10\n---\n\n# X\nbody")
    }

    @Test func resolveWithoutCommentWritesBareValue() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let fileURL = vault.appendingPathComponent("knowledge-base/research-queue/2026-06-10-x.md")
        try "---\ntitle: X\nstatus: open\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody"
            .write(to: fileURL, atomically: true, encoding: .utf8)
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: okRunner()), now: { Self.fixedDate() })
        try await writer.resolve(.dropped, fileURL: fileURL, label: "X")
        #expect(try String(contentsOf: fileURL, encoding: .utf8)
                == "---\ntitle: X\nstatus: dropped\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody")
    }

    @Test func statusCommitMessageMapping() {
        #expect(PerFileItemWriter.statusCommitMessage(.open, label: "T") == "app: reopen T")
        #expect(PerFileItemWriter.statusCommitMessage(.inProgress, label: "T") == "app: start T")
        #expect(PerFileItemWriter.statusCommitMessage(.done, label: "T") == "app: mark T done")
        #expect(PerFileItemWriter.statusCommitMessage(.dropped, label: "T") == "app: mark T dropped")
    }
}
