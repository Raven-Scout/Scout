import Foundation
import Testing
@testable import Scout

/// The launchd domain string (`gui/<uid>`) and the exit-code contract are the
/// whole surface here — get either wrong and schedule edits silently fail to
/// take effect.
@Suite("SystemLaunchctlClient")
struct SystemLaunchctlClientTests {

    private let plist = URL(fileURLWithPath: "/Users/alex/Library/LaunchAgents/com.scout.briefing.plist")

    // MARK: - bootout

    @Test("bootout targets gui/<uid> and hands back the raw exit code")
    func bootout_buildsCommandAndReturnsExitCode() async throws {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
        let client = SystemLaunchctlClient(runner: runner)

        let code = try await client.bootout(userUid: 501, plistPath: plist)

        #expect(code == 0)
        let call = try #require(runner.calls.first)
        #expect(call.executable == URL(fileURLWithPath: "/bin/launchctl"))
        #expect(call.arguments == ["bootout", "gui/501", plist.path])
    }

    @Test("a non-zero bootout is returned, not thrown — callers decide")
    func bootout_returnsNonZeroWithoutThrowing() async throws {
        // Exit 3 is launchctl's "not loaded", which callers swallow.
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 3, stdout: Data(), stderr: Data("not loaded".utf8)),
        ])
        #expect(try await SystemLaunchctlClient(runner: runner)
            .bootout(userUid: 501, plistPath: plist) == 3)
    }

    @Test("the uid is interpolated into the domain")
    func bootout_usesTheGivenUid() async throws {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
        _ = try await SystemLaunchctlClient(runner: runner)
            .bootout(userUid: 502, plistPath: plist)
        #expect(runner.calls.first?.arguments.contains("gui/502") == true)
    }

    // MARK: - bootstrap

    @Test("bootstrap targets gui/<uid> and succeeds on exit 0")
    func bootstrap_buildsCommand() async throws {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
        try await SystemLaunchctlClient(runner: runner)
            .bootstrap(userUid: 501, plistPath: plist)

        let call = try #require(runner.calls.first)
        #expect(call.executable == URL(fileURLWithPath: "/bin/launchctl"))
        #expect(call.arguments == ["bootstrap", "gui/501", plist.path])
    }

    @Test("a failed bootstrap throws with the exit code and stderr attached")
    func bootstrap_throwsOnNonZeroExit() async throws {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 5, stdout: Data(),
                          stderr: Data("Load failed: 5: Input/output error".utf8)),
        ])
        await #expect(throws: LaunchctlError.bootstrapFailed(
            exitCode: 5, stderr: "Load failed: 5: Input/output error")) {
            try await SystemLaunchctlClient(runner: runner)
                .bootstrap(userUid: 501, plistPath: plist)
        }
    }

    @Test("non-UTF8 stderr degrades to an empty message rather than failing")
    func bootstrap_nonUTF8StderrIsEmpty() async throws {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data([0xFF, 0xFE])),
        ])
        await #expect(throws: LaunchctlError.bootstrapFailed(exitCode: 1, stderr: "")) {
            try await SystemLaunchctlClient(runner: runner)
                .bootstrap(userUid: 501, plistPath: plist)
        }
    }

    @Test("bootstrap failures compare by exit code and stderr")
    func launchctlError_equality() {
        let a = LaunchctlError.bootstrapFailed(exitCode: 5, stderr: "boom")
        #expect(a == LaunchctlError.bootstrapFailed(exitCode: 5, stderr: "boom"))
        #expect(a != LaunchctlError.bootstrapFailed(exitCode: 6, stderr: "boom"))
        #expect(a != LaunchctlError.bootstrapFailed(exitCode: 5, stderr: "other"))
    }
}
