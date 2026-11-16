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

    /// A temp caches dir holding the old shared file and a new per-vault cache.
    private func seededCaches() throws -> (caches: URL, legacy: URL, current: URL) {
        let caches = FileManager.default.temporaryDirectory
            .appendingPathComponent("scout-legacy-cache-\(UUID().uuidString)", isDirectory: true)
        let legacy = caches.appendingPathComponent("Scout/session-parse-cache.json")
        let current = SessionLogService.parseCacheURL(
            cachesDirectory: caches,
            scoutDirectory: URL(fileURLWithPath: "/Users/alex/Scout", isDirectory: true),
            bundleIdentifier: SessionLogService.shippingBundleIdentifier
        )
        for file in [legacy, current] {
            try FileManager.default.createDirectory(
                at: file.deletingLastPathComponent(), withIntermediateDirectories: true
            )
            try Data("{}".utf8).write(to: file)
        }
        return (caches, legacy, current)
    }

    @Test func theShippingAppDeletesOnlyTheOldSharedFile() throws {
        let (caches, legacy, current) = try seededCaches()
        defer { try? FileManager.default.removeItem(at: caches) }
        #expect(SessionLogService.removeLegacyParseCache(
            cachesDirectory: caches, bundleIdentifier: "com.scout.Scout"
        ))
        #expect(!FileManager.default.fileExists(atPath: legacy.path))
        #expect(FileManager.default.fileExists(atPath: current.path))
        // A second launch finds nothing to do.
        #expect(!SessionLogService.removeLegacyParseCache(
            cachesDirectory: caches, bundleIdentifier: "com.scout.Scout"
        ))
    }

    @Test(arguments: ["com.scout.Scout.dev", "com.scout.ScoutTests", "com.example.scout", ""])
    func anyOtherBuildLeavesTheOldSharedFileAlone(bundle: String) throws {
        let (caches, legacy, current) = try seededCaches()
        defer { try? FileManager.default.removeItem(at: caches) }
        #expect(!SessionLogService.removeLegacyParseCache(cachesDirectory: caches, bundleIdentifier: bundle))
        #expect(FileManager.default.fileExists(atPath: legacy.path))
        #expect(FileManager.default.fileExists(atPath: current.path))
    }

    @Test func aMissingBundleIDLeavesTheOldSharedFileAlone() throws {
        let (caches, legacy, _) = try seededCaches()
        defer { try? FileManager.default.removeItem(at: caches) }
        #expect(!SessionLogService.removeLegacyParseCache(cachesDirectory: caches, bundleIdentifier: nil))
        #expect(FileManager.default.fileExists(atPath: legacy.path))
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
