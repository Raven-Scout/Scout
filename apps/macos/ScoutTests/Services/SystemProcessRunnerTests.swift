import Testing
import Foundation
@testable import Scout

@Suite("SystemProcessRunner")
struct SystemProcessRunnerTests {

    /// A child writing more than the ~64 KB pipe buffer before exiting must
    /// still complete. Reading stdout only after termination deadlocks: the
    /// child blocks on write, never exits, and the continuation never
    /// resumes — silently wedging whatever awaited it (issue #22 audit).
    @Test func drainsOutputLargerThanPipeBuffer() async throws {
        let runner = SystemProcessRunner()
        let result = await withTaskGroup(of: ProcessResult?.self) { group in
            group.addTask {
                try? await runner.run(
                    executable: URL(fileURLWithPath: "/bin/dd"),
                    arguments: ["if=/dev/zero", "bs=1024", "count=256"],
                    environment: [:],
                    workingDirectory: nil
                )
            }
            group.addTask {
                try? await Task.sleep(for: .seconds(15))
                return nil
            }
            let first = await group.next()!
            group.cancelAll()
            return first
        }
        let r = try #require(result, "runner deadlocked on >64KB output (timed out)")
        #expect(r.exitCode == 0)
        #expect(r.stdout.count == 256 * 1024)
    }

    @Test func capturesStdoutStderrAndExitCodeSeparately() async throws {
        let runner = SystemProcessRunner()
        let r = try await runner.run(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "echo out; echo err 1>&2; exit 3"],
            environment: [:],
            workingDirectory: nil
        )
        #expect(r.exitCode == 3)
        #expect(String(data: r.stdout, encoding: .utf8) == "out\n")
        #expect(String(data: r.stderr, encoding: .utf8) == "err\n")
    }
}
