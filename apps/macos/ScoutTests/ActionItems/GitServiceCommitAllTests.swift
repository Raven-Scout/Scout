import Testing
import Foundation
@testable import Scout

@Suite("GitService.commitAll")
struct GitServiceCommitAllTests {
    @Test func commitsWhenChangesExist() async throws {
        let repo = try makeRepo()
        defer { try? FileManager.default.removeItem(at: repo) }

        // Create an untracked file so git has something to commit.
        try "hello".write(to: repo.appendingPathComponent("file.txt"), atomically: true, encoding: .utf8)

        let service = GitService(repoURL: repo, runner: SystemProcessRunner())
        try await service.commitAll(message: "action-items: test commit")

        let log = try runGit(repo: repo, ["log", "-1", "--format=%s"])
        #expect(log.trimmingCharacters(in: .whitespacesAndNewlines) == "action-items: test commit")
    }

    @Test func noOpWhenCleanWorkingTree() async throws {
        let repo = try makeRepo()
        defer { try? FileManager.default.removeItem(at: repo) }
        let service = GitService(repoURL: repo, runner: SystemProcessRunner())

        // First commit so HEAD exists.
        try "a".write(to: repo.appendingPathComponent("a.txt"), atomically: true, encoding: .utf8)
        try await service.commitAll(message: "initial")
        let before = try runGit(repo: repo, ["rev-parse", "HEAD"])

        // commitAll with no changes must not error and must not create a new commit.
        try await service.commitAll(message: "should be no-op")
        let after = try runGit(repo: repo, ["rev-parse", "HEAD"])

        #expect(before == after)
    }

    @Test func returnsSilentlyWhenNotARepo() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let service = GitService(repoURL: dir, runner: SystemProcessRunner())
        try await service.commitAll(message: "no repo")  // must not throw
    }

    // MARK: - helpers

    private func makeRepo() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        _ = try runGit(repo: dir, ["init"])
        _ = try runGit(repo: dir, ["config", "user.email", "test@scout.local"])
        _ = try runGit(repo: dir, ["config", "user.name", "Scout Test"])
        return dir
    }

    private func runGit(repo: URL, _ args: [String]) throws -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["git", "-C", repo.path] + args
        let outPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = Pipe()
        try p.run()
        p.waitUntilExit()
        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        return String(data: data, encoding: .utf8) ?? ""
    }
}
