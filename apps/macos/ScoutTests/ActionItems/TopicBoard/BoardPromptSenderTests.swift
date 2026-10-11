import Testing
import Foundation
@testable import Scout

@Suite("Topic board — routing a note to the item's session")
struct BoardPromptSenderTests {
    @Test func routesBySession() {
        let uuid = "0f8fad5b-d9cb-469f-a165-70867728950e"
        let repo = URL(fileURLWithPath: "/tmp/repo", isDirectory: true)
        #expect(BoardPromptSender.route(for: uuid, folderFor: { $0 == uuid ? repo : nil }) == .resume(id: uuid, folder: repo))
        #expect(BoardPromptSender.route(for: uuid, folderFor: { _ in nil }) == .newSession)
        #expect(BoardPromptSender.route(for: "https://claude.ai/code/session_0123") == .open(URL(string: "https://claude.ai/code/session_0123")!))
        #expect(BoardPromptSender.route(for: nil) == .newSession)
        #expect(BoardPromptSender.route(for: "--dangerously-skip-permissions") == .newSession)
    }

    @Test func sessionFolderComesFromItsTranscript() throws {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: home) }
        let repo = home.appendingPathComponent("repo", isDirectory: true)
        let project = home.appendingPathComponent("projects/-tmp-repo", isDirectory: true)
        try FileManager.default.createDirectory(at: repo, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: project, withIntermediateDirectories: true)
        let id = "0f8fad5b-d9cb-469f-a165-70867728950e"
        try """
        {"type":"summary","summary":"no cwd here"}
        not json
        {"type":"user","cwd":"\(repo.path)","sessionId":"\(id)"}
        """.write(to: project.appendingPathComponent("\(id).jsonl"), atomically: true, encoding: .utf8)

        #expect(ClaudeSessionFolder.folder(forSessionID: id, claudeHome: home)?.path == repo.path)
        #expect(ClaudeSessionFolder.folder(forSessionID: "11111111-2222-3333-4444-555555555555", claudeHome: home) == nil)
        try FileManager.default.removeItem(at: repo)
        #expect(ClaudeSessionFolder.folder(forSessionID: id, claudeHome: home) == nil)
    }

    @Test func noteComesBeforeContext() {
        #expect(BoardPromptSender.prompt(note: "  merge it once green \n", context: "Help me…") == "merge it once green\n\nHelp me…")
    }

    @Test func confirmationSaysWhereTheNoteWent() {
        #expect(BoardPromptSender.confirmation(for: .newSession) == "Sent to Claude")
        #expect(BoardPromptSender.confirmation(for: .resume(id: "x", folder: URL(fileURLWithPath: "/tmp"))).contains("clipboard"))
    }
}
