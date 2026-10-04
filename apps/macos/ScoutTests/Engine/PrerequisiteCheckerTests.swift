import Testing
import Foundation
@testable import Scout

@Suite("PrerequisiteChecker")
struct PrerequisiteCheckerTests {
    func layout() throws -> EngineLayout {
        let l = EngineLayout(home: FileManager.default.temporaryDirectory.appendingPathComponent("prereq-\(UUID().uuidString)"))
        try FileManager.default.createDirectory(at: l.localBin, withIntermediateDirectories: true)
        return l
    }

    // gitCandidates/uvCandidates are always [] here — a real /opt/homebrew/bin/git
    // or /usr/local/bin/uv on the host or CI runner must never leak into a result
    // these tests assert on (Ruling 49).
    @Test func allPresentAndSignedIn() async throws {
        let l = try layout()
        try "#!/bin/sh\n".write(to: l.uvURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: l.uvURL.path)
        let runner = RuleBasedRunner()
        runner.on(tool: "claude", prefix: ["--version"], stdout: "2.1.259 (Claude Code)\n")
        runner.on(tool: "claude", prefix: ["auth", "status"], stdout: #"{"loggedIn": true}"#)
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "/Library/Developer/CommandLineTools\n")
        let checker = PrerequisiteChecker(
            runner: runner,
            layout: l,
            resolveClaude: { _ in "/Users/alex/.local/bin/claude" },
            gitCandidates: [],
            uvCandidates: []
        )
        let p = await checker.check()
        #expect(p.claude == .installed(path: URL(fileURLWithPath: "/Users/alex/.local/bin/claude"), version: "2.1.259"))
        #expect(p.auth == .signedIn)
        #expect(p.git == .present(URL(fileURLWithPath: "/usr/bin/git")))
        #expect(p.uv == .present(l.uvURL))
        #expect(p.canInstallEngine)
    }

    @Test func missingClaudeBlocksInstallAndLeavesAuthUnknown() async throws {
        let l = try layout()
        let runner = RuleBasedRunner()
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "", stderr: "xcode-select: error: unable to get active developer directory", exit: 2)
        let p = await PrerequisiteChecker(
            runner: runner,
            layout: l,
            resolveClaude: { _ in nil },
            gitCandidates: [],
            uvCandidates: []
        ).check()
        #expect(p.claude == .missing)
        #expect(p.auth == .unknown)
        #expect(p.git == .missing)
        #expect(p.uv == .missing)
        #expect(!p.canInstallEngine)
        #expect(runner.calls(to: "claude").isEmpty)
    }

    @Test func signedOutWhenAuthSaysSo() async throws {
        let l = try layout()
        let runner = RuleBasedRunner()
        runner.on(tool: "claude", prefix: ["--version"], stdout: "2.1.259 (Claude Code)\n")
        runner.on(tool: "claude", prefix: ["auth", "status"], stdout: #"{"loggedIn": false}"#, exit: 1)
        runner.on(tool: "xcode-select", prefix: ["-p"], stdout: "/Applications/Xcode.app/Contents/Developer\n")
        let p = await PrerequisiteChecker(
            runner: runner,
            layout: l,
            resolveClaude: { _ in "/opt/homebrew/bin/claude" },
            gitCandidates: [],
            uvCandidates: []
        ).check()
        #expect(p.auth == .signedOut)
        #expect(p.canInstallEngine)
    }
}
