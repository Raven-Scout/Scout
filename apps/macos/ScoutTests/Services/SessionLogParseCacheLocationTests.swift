import Testing
import Foundation
@testable import Scout

/// The run-log parse cache used to be one fixed file,
/// `~/Library/Caches/Scout/session-parse-cache.json`, shared by every build and
/// vault: a dev build or a second vault rewrote the installed app's cache, and
/// each then re-parsed every log. It is now one file per bundle ID and vault.
@Suite("Parse cache location")
struct SessionLogParseCacheLocationTests {
    private let caches = URL(fileURLWithPath: "/tmp/scout-caches-test", isDirectory: true)

    private func url(vault: String, bundle: String?) -> URL {
        SessionLogService.parseCacheURL(
            cachesDirectory: caches,
            scoutDirectory: URL(fileURLWithPath: vault, isDirectory: true),
            bundleIdentifier: bundle
        )
    }

    @Test func twoVaultsGetTwoCaches() {
        #expect(url(vault: "/Users/alex/Scout", bundle: "com.example.scout")
            != url(vault: "/Users/alex/Scout-test", bundle: "com.example.scout"))
    }

    @Test func twoBuildsOfOneVaultGetTwoCaches() {
        let installed = url(vault: "/Users/alex/Scout", bundle: "com.example.scout")
        let dev = url(vault: "/Users/alex/Scout", bundle: "com.example.scout.dev")
        #expect(installed != dev)
        #expect(installed.lastPathComponent == dev.lastPathComponent)  // same vault key
    }

    @Test func oneVaultSpelledTwoWaysSharesItsCache() {
        #expect(url(vault: "/Users/alex/Scout", bundle: "com.example.scout")
            == url(vault: "/Users/alex/./other/../Scout/", bundle: "com.example.scout"))
    }

    @Test func theCacheStaysUnderScoutsCacheFolderWithoutTheVaultPathInIt() {
        let u = url(vault: "/Users/alex/Scout", bundle: "com.example.scout")
        #expect(u.deletingLastPathComponent().path == "/tmp/scout-caches-test/Scout/parse-cache/com.example.scout")
        #expect(u.lastPathComponent.range(of: "^[0-9a-f]{16}\\.json$", options: .regularExpression) != nil)
        #expect(!u.path.contains("alex"))
    }

    @Test func anOddOrMissingBundleIDStaysOneSafePathComponent() {
        let odd = url(vault: "/Users/alex/Scout", bundle: "../evil/id")
        #expect(odd.deletingLastPathComponent().deletingLastPathComponent().path
            == "/tmp/scout-caches-test/Scout/parse-cache")
        #expect(!odd.deletingLastPathComponent().lastPathComponent.contains("/"))
        let missing = url(vault: "/Users/alex/Scout", bundle: nil)
        #expect(missing.deletingLastPathComponent().lastPathComponent == "unknown-bundle")
    }
}
