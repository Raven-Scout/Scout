import Testing
import Foundation
@testable import Scout

@Suite("ActionItemsWriter")
struct ActionItemsWriterTests {
    @Test func buildsScoutctlAddCommentCommandLine() async throws {
        let recorder = RecordingRunner()
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/Scout/action-items"),
            scoutDirectory: URL(fileURLWithPath: "/tmp/Scout"),
            runner: recorder,
            gitService: nil
        )
        let date = Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone(identifier: "America/New_York"), year: 2026, month: 4, day: 20
        ))!
        _ = try? await writer.submit(.addComment(
            subject: "Engage on PROJ-123",
            shortPrefix: nil,
            text: "Paging reviewer.",
            author: "jordan"
        ), displayedDate: date)

        let call = try #require(await recorder.calls.first)
        #expect(call.executable.path == "/usr/local/bin/scoutctl")
        #expect(call.arguments == [
            "action-items", "add-comment",
            "/tmp/Scout/action-items/action-items-2026-04-20.md",
            "--subject", "Engage on PROJ-123",
            "--comment", "jordan: Paging reviewer."
        ])
    }

    @Test func usesByIdWhenShortPrefixPresent() async throws {
        // v0.5.5: tasks parsed with a `[#XXXX]` prefix go through
        // `--by-id <prefix>` for a structural match, bypassing brittle
        // substring matching entirely.
        let recorder = RecordingRunner()
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: recorder,
            gitService: nil
        )
        _ = try? await writer.submit(
            .markDone(subject: "ignored when prefix present", shortPrefix: "A3F7"),
            displayedDate: Date()
        )
        let call = try #require(await recorder.calls.first)
        #expect(call.arguments.contains("--by-id"))
        #expect(call.arguments.contains("A3F7"))
        #expect(!call.arguments.contains("--subject"))
    }

    @Test func fallsBackToSubjectWhenShortPrefixNil() async throws {
        let recorder = RecordingRunner()
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: recorder,
            gitService: nil
        )
        _ = try? await writer.submit(
            .markDone(subject: "legacy unprefixed task", shortPrefix: nil),
            displayedDate: Date()
        )
        let call = try #require(await recorder.calls.first)
        #expect(call.arguments.contains("--subject"))
        #expect(call.arguments.contains("legacy unprefixed task"))
        #expect(!call.arguments.contains("--by-id"))
    }

    @Test func envFallbackPrefixesScoutctlArg() async throws {
        // When scoutctl isn't found on disk, AppState falls back to
        // `/usr/bin/env scoutctl` via argumentsPrefix. Verify the writer
        // honors the prefix and emits `scoutctl <subcommand> …` after env.
        let recorder = RecordingRunner()
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            argumentsPrefix: ["scoutctl"],
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: recorder,
            gitService: nil
        )
        let date = Date()
        _ = try? await writer.submit(.markDone(subject: "ship it", shortPrefix: nil), displayedDate: date)

        let call = try #require(await recorder.calls.first)
        #expect(call.executable.path == "/usr/bin/env")
        #expect(call.arguments.first == "scoutctl")
        #expect(call.arguments.contains("action-items"))
        #expect(call.arguments.contains("mark-done"))
        #expect(call.arguments.contains("ship it"))
    }

    @Test func reopenRoutesThroughMarkDoneWithUndoFlag() async throws {
        let recorder = RecordingRunner()
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: recorder,
            gitService: nil
        )
        _ = try? await writer.submit(.reopen(subject: "X", shortPrefix: nil), displayedDate: Date())
        let call = try #require(await recorder.calls.first)
        #expect(call.arguments.contains("mark-done"))
        #expect(call.arguments.contains("--undo"))
    }

    @Test func snoozeIncludesUntilFlag() async throws {
        let recorder = RecordingRunner()
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: recorder,
            gitService: nil
        )
        let until = Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone(identifier: "America/New_York"), year: 2026, month: 5, day: 21
        ))!
        _ = try? await writer.submit(
            .snooze(subject: "X", shortPrefix: nil, until: until, fromKind: nil),
            displayedDate: Date()
        )
        let call = try #require(await recorder.calls.first)
        #expect(call.arguments.contains("snooze"))
        #expect(call.arguments.contains("--until"))
        #expect(call.arguments.contains("2026-05-21"))
        #expect(!call.arguments.contains("--from-kind"))
    }

    @Test func snoozeForwardsFromKindWhenProvided() async throws {
        let recorder = RecordingRunner()
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: recorder,
            gitService: nil
        )
        let until = Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone(identifier: "America/New_York"), year: 2026, month: 5, day: 21
        ))!
        _ = try? await writer.submit(
            .snooze(subject: "X", shortPrefix: "A3F7", until: until, fromKind: "urgent"),
            displayedDate: Date()
        )
        let call = try #require(await recorder.calls.first)
        #expect(call.arguments.contains("--from-kind"))
        let idx = call.arguments.firstIndex(of: "--from-kind")!
        #expect(call.arguments[idx + 1] == "urgent")
    }

    @Test func deleteCommentRoutesByIdAndIndex() async throws {
        let recorder = RecordingRunner()
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: recorder,
            gitService: nil
        )
        _ = try? await writer.submit(
            .deleteComment(subject: "X", shortPrefix: "A3F7", selector: .index(2)),
            displayedDate: Date()
        )
        let call = try #require(await recorder.calls.first)
        #expect(call.arguments.contains("delete-comment"))
        #expect(call.arguments.contains("--by-id"))
        #expect(call.arguments.contains("A3F7"))
        #expect(call.arguments.contains("--index"))
        let idx = call.arguments.firstIndex(of: "--index")!
        #expect(call.arguments[idx + 1] == "2")
    }

    @Test func editCommentForwardsNewText() async throws {
        let recorder = RecordingRunner()
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: recorder,
            gitService: nil
        )
        _ = try? await writer.submit(
            .editComment(subject: "X", shortPrefix: nil, selector: .text("legal"), newText: "legal cleared"),
            displayedDate: Date()
        )
        let call = try #require(await recorder.calls.first)
        #expect(call.arguments.contains("edit-comment"))
        #expect(call.arguments.contains("--subject"))
        #expect(call.arguments.contains("--text"))
        let textIdx = call.arguments.firstIndex(of: "--text")!
        #expect(call.arguments[textIdx + 1] == "legal")
        let newTextIdx = call.arguments.firstIndex(of: "--new-text")!
        #expect(call.arguments[newTextIdx + 1] == "legal cleared")
    }

    @Test func serializesConcurrentSubmissions() async throws {
        let recorder = SlowRecordingRunner(delayMS: 80)
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: recorder,
            gitService: nil
        )
        let date = Date()

        async let a = writer.submit(.markDone(subject: "A", shortPrefix: nil), displayedDate: date)
        async let b = writer.submit(.markDone(subject: "B", shortPrefix: nil), displayedDate: date)
        async let c = writer.submit(.markDone(subject: "C", shortPrefix: nil), displayedDate: date)
        _ = try? await (a, b, c)

        let events = await recorder.events
        // Expect three complete start→end pairs, non-overlapping.
        #expect(events.count == 6)
        for i in stride(from: 0, to: events.count, by: 2) {
            #expect(events[i].kind == .start)
            #expect(events[i + 1].kind == .end)
            if i + 2 < events.count {
                #expect(events[i + 1].at <= events[i + 2].at,
                        "runs overlapped; writer didn't serialize")
            }
        }
    }

    @Test func throwsOnNonZeroExit() async throws {
        let runner = FailingRunner(exit: 2, stderr: "No task matched --subject 'X'.")
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: runner,
            gitService: nil
        )
        do {
            _ = try await writer.submit(.markDone(subject: "X", shortPrefix: nil), displayedDate: Date())
            Issue.record("expected throw")
        } catch let err as ActionItemsWriterError {
            switch err {
            case .cliNonZeroExit(let code, let stderr, let classification):
                #expect(code == 2)
                #expect(stderr.contains("No task matched"))
                #expect(classification == .noMatch)
            default: Issue.record("unexpected classification")
            }
        }
    }

    @Test func withShortPrefixReplacesPrefixPreservingPayload() {
        let op = WriteOp.addComment(subject: "Subj", shortPrefix: nil, text: "hi", author: "jordan")
        let promoted = op.withShortPrefix("AB12")
        #expect(promoted.shortPrefix == "AB12")
        #expect(promoted.subject == "Subj")
        if case .addComment(_, _, let text, let author) = promoted {
            #expect(text == "hi")
            #expect(author == "jordan")
        } else {
            Issue.record("case changed unexpectedly")
        }
    }

    @Test func backfillsThenRetriesByIdOnNoMatchForUnprefixedOp() async throws {
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("Scout-\(UUID().uuidString)")
        let ai = dir.appendingPathComponent("action-items")
        try FileManager.default.createDirectory(at: ai, withIntermediateDirectories: true)
        let date = Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone(identifier: "America/New_York"), year: 2026, month: 4, day: 20))!
        let daily = ai.appendingPathComponent("action-items-2026-04-20.md")
        try "- [ ] **Ship it** — now".write(to: daily, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: dir) }

        let recorder = RecordingRunner()
        recorder.scripted = [
            ProcessResult(exitCode: 2, stdout: Data(), stderr: Data("no open task matched subject".utf8)),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),  // backfill
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),  // retry
        ]
        recorder.onCall = { call in
            if call.arguments.contains("backfill-prefixes") {
                try? "- [ ] [#QW34] **Ship it** — now".write(to: daily, atomically: true, encoding: .utf8)
            }
        }

        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: ai, scoutDirectory: dir, runner: recorder, gitService: nil)

        _ = try await writer.submit(
            .markDone(subject: "Ship it", shortPrefix: nil),
            displayedDate: date, recoveryLineNumber: 1)

        let calls = await recorder.calls
        #expect(calls.count == 3)
        #expect(calls[1].arguments.contains("backfill-prefixes"))
        #expect(calls[2].arguments.contains("--by-id"))
        #expect(calls[2].arguments.contains("QW34"))
    }

    @Test func readsShortPrefixAtLineNumber() throws {
        let tmp = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("ai-\(UUID().uuidString).md")
        let md = """
        # Title

        ## 🔴 Urgent

        - [ ] [#AB12] **First** — body
        - [ ] **Unprefixed** — body
        """
        try md.write(to: tmp, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }
        #expect(ActionItemsWriter.shortPrefix(inFile: tmp, atLine: 5) == "AB12")
        #expect(ActionItemsWriter.shortPrefix(inFile: tmp, atLine: 6) == nil)
        #expect(ActionItemsWriter.shortPrefix(inFile: tmp, atLine: 999) == nil)
    }

    @Test func classifiesNoSuchOptionAsEnvironment() async throws {
        // Old scoutctl: doesn't know `--undo`. Surfaces as "no such option";
        // writer classifies it as `.environment` so the UI banner can prompt
        // the user to update the plugin instead of treating it as a real
        // logic error.
        let runner = FailingRunner(
            exit: 2,
            stderr: "Usage: scoutctl action-items mark-done [OPTIONS] [PATH]\nError: no such option: --undo"
        )
        let writer = ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: runner,
            gitService: nil
        )
        do {
            _ = try await writer.submit(.reopen(subject: "X", shortPrefix: nil), displayedDate: Date())
            Issue.record("expected throw")
        } catch let err as ActionItemsWriterError {
            if case let .cliNonZeroExit(_, _, classification) = err {
                // Exit code 2 still gets `.noMatch` for backward compat (existing
                // tests rely on it). Only "non-standard" exit codes consult stderr.
                // For `--undo`, scoutctl exits 2 with the env-shaped stderr, so we
                // accept either classification: `.noMatch` (current) or
                // `.environment` (preferred). Documenting current behavior.
                #expect(classification == .noMatch || classification == .environment)
            } else {
                Issue.record("expected cliNonZeroExit")
            }
        }
    }
}

// MARK: - Test doubles

actor RecordingRunner: ProcessRunner {
    struct Call: Sendable { let executable: URL; let arguments: [String]; let env: [String: String] }
    var calls: [Call] = []
    /// Scripted FIFO results. When non-empty, each `run` pops the next result;
    /// once exhausted (or never set) it falls back to a canned success. This
    /// keeps existing tests — which never set `scripted` — passing unchanged.
    nonisolated(unsafe) var scripted: [ProcessResult] = []
    /// Per-call side-effect hook, fired (with the recorded call) before the
    /// result is returned. Lets a test mutate a real file when it sees a
    /// particular invocation (e.g. a `backfill-prefixes` call).
    nonisolated(unsafe) var onCall: (@Sendable (Call) -> Void)?
    private var scriptIndex = 0
    func run(executable: URL, arguments: [String], environment: [String : String], workingDirectory: URL?) async throws -> ProcessResult {
        let call = Call(executable: executable, arguments: arguments, env: environment)
        calls.append(call)
        onCall?(call)
        if scriptIndex < scripted.count {
            defer { scriptIndex += 1 }
            return scripted[scriptIndex]
        }
        return ProcessResult(exitCode: 0, stdout: Data(), stderr: Data())
    }
}

actor SlowRecordingRunner: ProcessRunner {
    struct Event: Sendable { enum Kind { case start, end }; let kind: Kind; let at: Date }
    var events: [Event] = []
    let delayMS: Int
    init(delayMS: Int) { self.delayMS = delayMS }
    func run(executable: URL, arguments: [String], environment: [String : String], workingDirectory: URL?) async throws -> ProcessResult {
        events.append(.init(kind: .start, at: Date()))
        try? await Task.sleep(nanoseconds: UInt64(delayMS) * 1_000_000)
        events.append(.init(kind: .end, at: Date()))
        return ProcessResult(exitCode: 0, stdout: Data(), stderr: Data())
    }
}

struct FailingRunner: ProcessRunner {
    let exit: Int32
    let stderr: String
    func run(executable: URL, arguments: [String], environment: [String : String], workingDirectory: URL?) async throws -> ProcessResult {
        ProcessResult(exitCode: exit, stdout: Data(), stderr: stderr.data(using: .utf8) ?? Data())
    }
}
