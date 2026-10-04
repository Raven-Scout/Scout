import Foundation
import Testing
@testable import Scout

@Suite("SessionsRefresh")
struct SessionsRefreshTests {
    private let cache = URL(fileURLWithPath: "/Users/alex/Scout/.scout-cache", isDirectory: true)

    private func url(_ name: String, in dir: URL? = nil) -> URL {
        (dir ?? cache).appendingPathComponent(name)
    }

    @Test func theFastLaneSkipsGhAndThePRLaneDoesNot() {
        #expect(SessionsRefresh.fastArguments(prefix: []) == ["session", "index", "--json", "--no-gh"])
        #expect(SessionsRefresh.prArguments(prefix: ["scoutctl"]) == ["scoutctl", "session", "index", "--json"])
    }

    @Test(arguments: [
        "sessions-index.json",
        "sessions-pr.cache.json",
        "sessions-desktop.cache.json",
        "sessions-transcripts.cache.json",
        ".sessions-index.json.k3j9x2qa.tmp",
        ".sessions-pr.cache.json.ab_12cd9.tmp",
    ])
    func theEnginesOwnWritesAreIgnored(name: String) {
        #expect(SessionsRefresh.isEngineOwnWrite(url(name), cacheDirectory: cache))
    }

    @Test(arguments: [
        "cc-sessions.md",
        "connector-alerts-acked.json",
        "sessions-index.json.bak",
        ".sessions-index.json.tmp",
        "my-sessions-index.json",
    ])
    func otherCacheFilesAreNot(name: String) {
        #expect(!SessionsRefresh.isEngineOwnWrite(url(name), cacheDirectory: cache))
    }

    @Test func theSameNameOutsideTheCacheDirectoryIsNotAnOwnWrite() {
        let elsewhere = URL(fileURLWithPath: "/Users/alex/.claude/projects/-Users-alex-Scout", isDirectory: true)
        #expect(!SessionsRefresh.isEngineOwnWrite(url("sessions-index.json", in: elsewhere), cacheDirectory: cache))
    }

    @Test func aSymlinkedCacheDirectoryStillMatches() throws {
        let fm = FileManager.default
        let base = fm.temporaryDirectory.appendingPathComponent("sessions-refresh-\(UUID().uuidString)")
        let real = base.appendingPathComponent("real/.scout-cache", isDirectory: true)
        try fm.createDirectory(at: real, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: base) }
        let link = base.appendingPathComponent("link")
        try fm.createSymbolicLink(at: link, withDestinationURL: base.appendingPathComponent("real"))
        let viaLink = link.appendingPathComponent(".scout-cache", isDirectory: true)
        #expect(SessionsRefresh.isEngineOwnWrite(real.appendingPathComponent("sessions-index.json"),
                                                 cacheDirectory: viaLink))
    }

    @Test func watchRootsAreTheThreeSourceDirectories() {
        let home = URL(fileURLWithPath: "/Users/alex/.claude", isDirectory: true)
        let support = URL(fileURLWithPath: "/Users/alex/Library/Application Support/Claude", isDirectory: true)
        #expect(SessionsRefresh.watchRoots(claudeHome: home, desktopSupport: support).map(\.path) == [
            "/Users/alex/Library/Application Support/Claude/claude-code-sessions",
            "/Users/alex/.claude/sessions",
            "/Users/alex/.claude/projects",
        ])
    }
}
