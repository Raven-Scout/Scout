import Foundation
import Testing
@testable import Scout

/// A runner that always fails to spawn, exercising the `.processFailed` path.
private struct SpawnFailingRunner: ProcessRunner {
    struct Boom: Error {}
    func run(
        executable: URL, arguments: [String],
        environment: [String: String], workingDirectory: URL?
    ) async throws -> ProcessResult {
        throw Boom()
    }
}

/// scoutctl's exit code decides which banner the UI shows — "couldn't find
/// that task", "that matched several", or "your plugin is out of date". These
/// cover the mapping for every code the writer knows about.
@Suite("ActionItemsWriter — failure classification")
struct ActionItemsWriterClassificationTests {

    private func writer(runner: any ProcessRunner) -> ActionItemsWriter {
        ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: runner,
            gitService: nil)
    }

    /// Run a write that is expected to fail, and hand back the classification.
    private func classification(
        exit: Int32, stderr: String
    ) async throws -> ActionItemsWriterError.Classification? {
        do {
            _ = try await writer(runner: FailingRunner(exit: exit, stderr: stderr))
                .submit(.markDone(subject: "X", shortPrefix: "AB12"), displayedDate: Date())
            Issue.record("expected the write to throw for exit \(exit)")
            return nil
        } catch let error as ActionItemsWriterError {
            guard case let .cliNonZeroExit(code, text, classification) = error else {
                Issue.record("expected .cliNonZeroExit, got \(error)")
                return nil
            }
            #expect(code == exit)
            #expect(text == stderr)
            return classification
        }
    }

    @Test("exit 3 means the subject matched more than one task")
    func classify_exitThreeIsAmbiguous() async throws {
        #expect(try await classification(exit: 3, stderr: "Multiple tasks matched.") == .ambiguous)
    }

    @Test("the ordinary failure codes classify as .other")
    func classify_ordinaryFailureCodes() async throws {
        #expect(try await classification(exit: 1, stderr: "boom") == .other)
        #expect(try await classification(exit: 4, stderr: "boom") == .other)
        #expect(try await classification(exit: 5, stderr: "boom") == .other)
    }

    @Test("an unknown exit code with environment-shaped stderr classifies as .environment")
    func classify_unknownCodeWithEnvironmentStderr() async throws {
        #expect(try await classification(
            exit: 127, stderr: "zsh: command not found: scoutctl") == .environment)
        #expect(try await classification(
            exit: 64, stderr: "Error: no such option: --undo") == .environment)
        #expect(try await classification(
            exit: 99, stderr: "ModuleNotFoundError: No module named 'scoutctl'") == .environment)
    }

    @Test("environment detection ignores case")
    func classify_environmentDetectionIsCaseInsensitive() async throws {
        #expect(try await classification(
            exit: 127, stderr: "COMMAND NOT FOUND") == .environment)
    }

    @Test("an unknown exit code with unrelated stderr falls back to .other")
    func classify_unknownCodeWithUnrelatedStderr() async throws {
        #expect(try await classification(
            exit: 42, stderr: "the disk caught fire") == .other)
        #expect(try await classification(exit: 42, stderr: "") == .other)
    }

    // MARK: - processFailed

    @Test("a runner that can't spawn surfaces as .processFailed")
    func spawnFailureIsProcessFailed() async throws {
        do {
            _ = try await writer(runner: SpawnFailingRunner())
                .submit(.markDone(subject: "X", shortPrefix: "AB12"), displayedDate: Date())
            Issue.record("expected the write to throw")
        } catch let error as ActionItemsWriterError {
            guard case .processFailed = error else {
                Issue.record("expected .processFailed, got \(error)")
                return
            }
        }
    }

    // MARK: - Equatable

    @Test("two CLI failures are equal only when code, stderr, and class all match")
    func equality_cliNonZeroExit() {
        let a = ActionItemsWriterError.cliNonZeroExit(
            exitCode: 2, stderr: "no match", classification: .noMatch)
        #expect(a == ActionItemsWriterError.cliNonZeroExit(
            exitCode: 2, stderr: "no match", classification: .noMatch))
        #expect(a != ActionItemsWriterError.cliNonZeroExit(
            exitCode: 3, stderr: "no match", classification: .noMatch))
        #expect(a != ActionItemsWriterError.cliNonZeroExit(
            exitCode: 2, stderr: "different", classification: .noMatch))
        #expect(a != ActionItemsWriterError.cliNonZeroExit(
            exitCode: 2, stderr: "no match", classification: .ambiguous))
    }

    @Test("all spawn failures compare equal — the underlying error isn't Equatable")
    func equality_processFailedIgnoresUnderlyingError() {
        struct A: Error {}
        struct B: Error {}
        #expect(ActionItemsWriterError.processFailed(A())
                == ActionItemsWriterError.processFailed(B()))
    }

    @Test("a CLI failure never equals a spawn failure")
    func equality_acrossCasesIsFalse() {
        struct A: Error {}
        let cli = ActionItemsWriterError.cliNonZeroExit(
            exitCode: 1, stderr: "", classification: .other)
        #expect(cli != ActionItemsWriterError.processFailed(A()))
        #expect(ActionItemsWriterError.processFailed(A()) != cli)
    }

    @Test("classification values are distinguishable")
    func classificationValues_areDistinct() {
        let all: [ActionItemsWriterError.Classification] =
            [.noMatch, .ambiguous, .environment, .other]
        for (i, lhs) in all.enumerated() {
            for (j, rhs) in all.enumerated() {
                #expect((lhs == rhs) == (i == j))
            }
        }
    }
}
