import Testing
import Foundation
@testable import Scout

@Suite("ActionItemsEnvironmentCheck")
struct ActionItemsEnvironmentCheckTests {
    @Test func passesWhenScoutctlActionItemsHelpExitsZero() async throws {
        let runner = StubRunner(result: ProcessResult(
            exitCode: 0,
            stdout: "Usage: scoutctl action-items …".data(using: .utf8)!,
            stderr: Data()
        ))
        let check = ActionItemsEnvironmentCheck(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            runner: runner
        )
        let result = try await check.run()
        #expect(result.ok)
        #expect(result.message == nil)
    }

    @Test func failsWithStaleScoutctlMessage() async throws {
        // Old scoutctl with no `action-items` group — surfaces as
        // "no such command" on stderr.
        let runner = StubRunner(result: ProcessResult(
            exitCode: 2,
            stdout: Data(),
            stderr: "Error: no such command 'action-items'".data(using: .utf8)!
        ))
        let check = ActionItemsEnvironmentCheck(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            runner: runner
        )
        let result = try await check.run()
        #expect(!result.ok)
        #expect(result.message?.contains("too old") == true)
    }

    @Test func failsWithGenericMessageOnOtherErrors() async throws {
        let runner = StubRunner(result: ProcessResult(
            exitCode: 5,
            stdout: Data(),
            stderr: "Traceback (something else)".data(using: .utf8)!
        ))
        let check = ActionItemsEnvironmentCheck(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            runner: runner
        )
        let result = try await check.run()
        #expect(!result.ok)
        #expect(result.message?.contains("Traceback") == true)
    }

    @Test func failsWhenScoutctlNotFound() async throws {
        struct ThrowingRunner: ProcessRunner {
            func run(executable: URL, arguments: [String], environment: [String : String], workingDirectory: URL?) async throws -> ProcessResult {
                throw NSError(domain: NSPOSIXErrorDomain, code: 2, userInfo: nil)  // ENOENT
            }
        }
        let check = ActionItemsEnvironmentCheck(
            scoutctl: URL(fileURLWithPath: "/nonexistent/scoutctl"),
            runner: ThrowingRunner()
        )
        let result = try await check.run()
        #expect(!result.ok)
        #expect(result.message?.contains("scoutctl not found") == true)
    }
}

struct StubRunner: ProcessRunner {
    let result: ProcessResult
    func run(executable: URL, arguments: [String], environment: [String : String], workingDirectory: URL?) async throws -> ProcessResult {
        result
    }
}
