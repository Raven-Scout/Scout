import Testing
import Foundation
@testable import Scout

@Suite("ClaudeLauncher desktop URLs")
struct ClaudeDesktopURLTests {

    private func queryItems(_ url: URL) -> [String: String] {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems ?? []
        return Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value ?? "") })
    }

    @Test func chatURL_hostAndPromptQuery() throws {
        let url = try #require(ClaudeLauncher.makeDesktopURL(
            prompt: "hello", mode: .chat))
        #expect(url.scheme == "claude")
        #expect(url.host == "claude.ai")
        #expect(url.path == "/new")
        #expect(queryItems(url) == ["q": "hello"])
    }

    @Test func coworkURL_hostAndPromptQuery() throws {
        let url = try #require(ClaudeLauncher.makeDesktopURL(
            prompt: "hello", mode: .cowork))
        #expect(url.scheme == "claude")
        #expect(url.host == "cowork")
        #expect(url.path == "/new")
        #expect(queryItems(url) == ["q": "hello"])
    }

    @Test func codeURL_hostPromptAndFolderQuery() throws {
        let folder = URL(fileURLWithPath: "/Users/me/Scout")
        let url = try #require(ClaudeLauncher.makeDesktopURL(
            prompt: "fix the bug", mode: .code(folder: folder)))
        #expect(url.scheme == "claude")
        #expect(url.host == "code")
        #expect(url.path == "/new")
        #expect(queryItems(url) == [
            "q": "fix the bug",
            "folder": "/Users/me/Scout",
        ])
    }

    @Test func codeURL_promptWithMetacharactersRoundTrips() throws {
        // &, #, ?, =, newlines and spaces must survive URLComponents
        // percent-encoding and decode back to the original prompt.
        let prompt = "line one\nA & B? #tag = 100% done"
        let folder = URL(fileURLWithPath: "/tmp/with space/vault")
        let url = try #require(ClaudeLauncher.makeDesktopURL(
            prompt: prompt, mode: .code(folder: folder)))
        let items = queryItems(url)
        #expect(items["q"] == prompt)
        #expect(items["folder"] == "/tmp/with space/vault")
    }
}
