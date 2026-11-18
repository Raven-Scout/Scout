import Foundation
import Testing
@testable import Scout

extension SessionsFixture {
    /// The fixture with some sessions' fields replaced (`NSNull()` clears
    /// one), and optionally extra projects and sessions appended.
    static func index(
        patching patches: [String: [String: Any]],
        addingProjects projects: [[String: Any]] = [],
        addingSessions sessions: [[String: Any]] = []
    ) throws -> SessionIndex {
        var object = try object()
        var all = try #require(object["sessions"] as? [[String: Any]])
        for i in all.indices {
            guard let id = all[i]["id"] as? String, let patch = patches[id] else { continue }
            for (key, value) in patch { all[i][key] = value }
        }
        object["sessions"] = all + sessions
        object["projects"] = (try #require(object["projects"] as? [[String: Any]])) + projects
        return try SessionIndex.decode(encode(object))
    }

    /// A scratch folder under the temp directory, removed by the caller.
    static func scratch() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("sessions-resume-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// A one-line transcript whose first entry records `cwd`.
    @discardableResult
    static func writeTranscript(at url: URL, cwd: URL) throws -> URL {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let line = try JSONSerialization.data(withJSONObject: ["type": "user", "cwd": cwd.path])
        try (line + Data("\n".utf8)).write(to: url)
        return url
    }
}

/// UI audit #32 and #36: Resume runs from the folder the session ran in,
/// says so when that folder is gone, and launches through an injected
/// launcher so no test ever opens a terminal.
@MainActor
@Suite("Session resume")
struct SessionResumeTests {
    private let id = "local_A"
    private let config = CLIConfig(claudePathOverride: "", terminal: .auto, customCommand: "")

    private func session(_ patch: [String: Any]) throws -> AgentSession {
        try #require(SessionsFixture.session(id, in: SessionsFixture.index(patching: [id: patch])))
    }

    // MARK: Folder lookup

    @Test func theTranscriptsFolderWinsOverTheIndexCwd() throws {
        let root = try SessionsFixture.scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let ran = root.appendingPathComponent("worktree", isDirectory: true)
        try FileManager.default.createDirectory(at: ran, withIntermediateDirectories: true)
        let transcript = try SessionsFixture.writeTranscript(at: root.appendingPathComponent("t/a.jsonl"), cwd: ran)
        let s = try session(["transcript": ["path": transcript.path, "first_prompt": "", "files_touched": [],
                                            "tool_calls": 0, "last_turn": ["at": NSNull(), "kind": "unknown"],
                                            "mtime_ns": 0]])
        #expect(SessionsFormat.resumeFolder(for: s, claudeHome: nil, exists: { _ in true }) == .own(ran))
    }

    @Test func withoutATranscriptPathTheClaudeHomeIsSearchedByID() throws {
        let root = try SessionsFixture.scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let ran = root.appendingPathComponent("repo", isDirectory: true)
        try FileManager.default.createDirectory(at: ran, withIntermediateDirectories: true)
        let s = try session(["transcript": NSNull()])
        let cliID = try #require(s.cliSessionID)
        try SessionsFixture.writeTranscript(
            at: root.appendingPathComponent("claude/projects/-x-repo/\(cliID).jsonl"), cwd: ran)
        #expect(SessionsFormat.resumeFolder(for: s, claudeHome: root.appendingPathComponent("claude"),
                                            exists: { _ in false }) == .own(ran))
    }

    @Test func thenTheIndexCwdThenTheProjectFolderThenNothing() throws {
        let s = try session(["transcript": NSNull()])
        #expect(SessionsFormat.resumeFolder(for: s, claudeHome: nil, exists: { _ in true })
                == .own(URL(fileURLWithPath: s.cwd, isDirectory: true)))
        #expect(SessionsFormat.resumeFolder(for: s, claudeHome: nil, exists: { $0 == s.originCwd })
                == .project(URL(fileURLWithPath: s.originCwd, isDirectory: true)))
        // Never the home folder, where `claude --resume` can't find it.
        #expect(SessionsFormat.resumeFolder(for: s, claudeHome: nil, exists: { _ in false }) == nil)
    }

    @Test func aTranscriptWhoseFolderIsGoneFallsThrough() throws {
        let root = try SessionsFixture.scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let gone = root.appendingPathComponent("removed-worktree", isDirectory: true)
        let transcript = try SessionsFixture.writeTranscript(at: root.appendingPathComponent("t/a.jsonl"), cwd: gone)
        let s = try session(["transcript": ["path": transcript.path, "first_prompt": "", "files_touched": [],
                                            "tool_calls": 0, "last_turn": ["at": NSNull(), "kind": "unknown"],
                                            "mtime_ns": 0]])
        #expect(SessionsFormat.resumeFolder(for: s, claudeHome: nil, exists: { $0 == s.originCwd })
                == .project(URL(fileURLWithPath: s.originCwd, isDirectory: true)))
    }

    // MARK: Running it

    @Test func resumeLaunchesInTheSessionsOwnFolderWithNoNote() async throws {
        let root = try SessionsFixture.scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let s = try session(["transcript": NSNull(), "cwd": root.path])
        var launched: [(String, URL)] = []
        let note = try await SessionResume.run(s, config: config, claudeHome: nil) { id, folder, _ in
            launched.append((id, folder))
        }
        #expect(note == nil)
        #expect(launched.count == 1)
        #expect(launched.first?.0 == s.cliSessionID)
        #expect(launched.first?.1.standardizedFileURL.path == root.standardizedFileURL.path)
    }

    @Test func resumeFromTheProjectFolderSaysSo() async throws {
        let root = try SessionsFixture.scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let s = try session(["transcript": NSNull(), "cwd": root.appendingPathComponent("gone").path,
                             "origin_cwd": root.path])
        var folders: [URL] = []
        let note = try await SessionResume.run(s, config: config, claudeHome: nil) { _, folder, _ in folders.append(folder) }
        #expect(folders.map(\.path) == [root.path])
        #expect(note?.contains("project folder") == true)
    }

    @Test func aMissingFolderIsReportedAndNothingLaunches() async throws {
        let root = try SessionsFixture.scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let gone = root.appendingPathComponent("gone").path
        let s = try session(["transcript": NSNull(), "cwd": gone, "origin_cwd": gone + "-too"])
        var launches = 0
        await #expect(throws: SessionResume.Failure.folderMissing(gone)) {
            _ = try await SessionResume.run(s, config: config, claudeHome: nil) { _, _, _ in launches += 1 }
        }
        #expect(launches == 0)
        #expect(SessionResume.Failure.folderMissing(gone).localizedDescription.contains(gone))
    }

    @Test func aSessionWithoutACLIIDIsRefused() async throws {
        let s = try session(["cli_session_id": NSNull()])
        await #expect(throws: SessionResume.Failure.noCLISession) {
            _ = try await SessionResume.run(s, config: config, claudeHome: nil) { _, _, _ in
                Issue.record("must not launch")
            }
        }
    }

    @Test func aLaunchFailureReachesTheCaller() async throws {
        let root = try SessionsFixture.scratch()
        defer { try? FileManager.default.removeItem(at: root) }
        let s = try session(["transcript": NSNull(), "cwd": root.path])
        await #expect(throws: ClaudeLauncher.LaunchError.accessibilityDenied) {
            _ = try await SessionResume.run(s, config: config, claudeHome: nil) { _, _, _ in
                throw ClaudeLauncher.LaunchError.accessibilityDenied
            }
        }
    }
}

/// UI audit #38: launch failures name their cause and, for a missing
/// permission, the System Settings pane that fixes it.
@Suite("Launch failures")
struct LaunchFailureTests {
    @Test func appleScriptPermissionErrorsAreRecognised() {
        #expect(ClaudeLauncher.appleScriptError(number: -1743, message: "Not authorised") == .automationDenied("Not authorised"))
        #expect(ClaudeLauncher.appleScriptError(number: -1719, message: "no assistive access") == .accessibilityDenied)
        #expect(ClaudeLauncher.appleScriptError(number: -25211, message: nil) == .accessibilityDenied)
        #expect(ClaudeLauncher.appleScriptError(number: -2700, message: "boom") == .terminalLaunchFailed("boom"))
        #expect(ClaudeLauncher.appleScriptError(number: nil, message: nil) == .terminalLaunchFailed("unknown AppleScript error"))
    }

    @Test func permissionErrorsPointAtTheirSettingsPane() {
        #expect(ClaudeLauncher.LaunchError.accessibilityDenied.settingsURL?.absoluteString.hasSuffix("Privacy_Accessibility") == true)
        #expect(ClaudeLauncher.LaunchError.automationDenied("x").settingsURL?.absoluteString.hasSuffix("Privacy_Automation") == true)
        #expect(ClaudeLauncher.LaunchError.ghosttyLaunchFailed("x").settingsURL == nil)
        #expect(ClaudeLauncher.LaunchError.ghosttyNotInstalled.settingsURL == nil)
    }

    @Test func aLaunchFailureCarriesTheTextAndThePane() {
        let denied = LaunchFailure(ClaudeLauncher.LaunchError.accessibilityDenied)
        #expect(denied.message.contains("Accessibility"))
        #expect(denied.settingsURL != nil)
        let other = LaunchFailure(SessionResume.Failure.noCLISession)
        #expect(other.settingsURL == nil)
        #expect(!other.message.isEmpty)
        #expect(LaunchFailure(ClaudeLauncher.LaunchError.ghosttyLaunchFailed("The app is damaged")).message
                == "Couldn't open Ghostty: The app is damaged")
    }

    @Test func theGhosttyTabScriptEscapesTheFolderAndCommand() {
        let script = GhosttyLauncher.makeNewTabScript(
            cwd: URL(fileURLWithPath: "/tmp/a \"quoted\" dir"), runningCommand: #"git diff a^..b"#, delay: 0.3)
        #expect(script.hasPrefix("delay 0.3\n"))
        // Shell-quoted folder, then AppleScript-escaped: the same two passes
        // the launcher always made.
        #expect(script.contains(#"keystroke "cd \"/tmp/a \\\"quoted\\\" dir\" && git diff a^..b""#))
        #expect(script.contains(#"tell process "Ghostty""#))
    }
}
