import Testing
import Foundation
@testable import Scout

/// App audit C11: the run-log parse cache is keyed by bundle ID and vault, so
/// a dev build or a second vault never overwrites the installed app's cache.
@Suite("SessionLogService parse cache location")
struct SessionLogParseCacheScopeTests {
    private let caches = URL(fileURLWithPath: "/tmp/caches", isDirectory: true)
    private let vaultA = URL(fileURLWithPath: "/Users/someone/Scout", isDirectory: true)
    private let vaultB = URL(fileURLWithPath: "/Users/someone/Second Vault", isDirectory: true)

    @Test func differentBundleIDs_getDifferentFiles() {
        let shipping = SessionLogService.parseCacheURL(cachesDirectory: caches, bundleID: "com.scout.Scout", vault: vaultA)
        let dev = SessionLogService.parseCacheURL(cachesDirectory: caches, bundleID: "com.scout.Scout.dev", vault: vaultA)
        #expect(shipping != dev)
        #expect(shipping.deletingLastPathComponent().lastPathComponent == "com.scout.Scout")
        #expect(dev.deletingLastPathComponent().lastPathComponent == "com.scout.Scout.dev")
    }

    @Test func differentVaults_getDifferentFiles() {
        let a = SessionLogService.parseCacheURL(cachesDirectory: caches, bundleID: "com.scout.Scout", vault: vaultA)
        let b = SessionLogService.parseCacheURL(cachesDirectory: caches, bundleID: "com.scout.Scout", vault: vaultB)
        #expect(a != b)
        #expect(a.deletingLastPathComponent() == b.deletingLastPathComponent())
    }

    @Test func sameVault_isStable_andSpellingInsensitive() {
        let a = SessionLogService.parseCacheURL(cachesDirectory: caches, bundleID: "x", vault: vaultA)
        let again = SessionLogService.parseCacheURL(
            cachesDirectory: caches, bundleID: "x",
            vault: URL(fileURLWithPath: "/Users/someone/./Scout/", isDirectory: true))
        #expect(a == again)
    }

    @Test func fileName_neverContainsTheVaultPath() {
        let url = SessionLogService.parseCacheURL(cachesDirectory: caches, bundleID: "com.scout.Scout", vault: vaultB)
        #expect(!url.lastPathComponent.contains("Second"))
        #expect(url.lastPathComponent.hasPrefix("session-parse-cache-"))
        #expect(url.lastPathComponent.hasSuffix(".json"))
        #expect(SessionLogService.vaultKey(vaultB).count == 16)
    }

    @Test func missingBundleID_fallsBackToScoutFolder() {
        let url = SessionLogService.parseCacheURL(cachesDirectory: caches, bundleID: nil, vault: vaultA)
        #expect(url.deletingLastPathComponent().lastPathComponent == "Scout")
    }

    @Test func legacySharedFile_isRemovedOnlyByTheShippingApp() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let legacy = dir.appendingPathComponent("Scout/session-parse-cache.json")
        try FileManager.default.createDirectory(at: legacy.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: legacy)

        SessionLogService.removeLegacySharedParseCache(bundleID: "com.scout.Scout.dev", cachesDirectory: dir)
        #expect(FileManager.default.fileExists(atPath: legacy.path), "a dev build must not touch the installed app's file")

        SessionLogService.removeLegacySharedParseCache(bundleID: "com.scout.Scout", cachesDirectory: dir)
        #expect(!FileManager.default.fileExists(atPath: legacy.path))
    }
}
