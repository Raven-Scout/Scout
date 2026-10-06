import Testing
import Foundation
@testable import Scout

@Suite("ClaudePluginsRegistry")
struct ClaudePluginsRegistryTests {
    // Fixtures load by name, not by subfolder — the test bundle flattens
    // resources, so a `Fixtures/claude-plugins` subpath does not resolve there.
    static let installedPluginsURL = Bundle(for: FixtureAnchor.self).url(forResource: "claude-installed-plugins", withExtension: "json")!
    static let knownMarketplacesURL = Bundle(for: FixtureAnchor.self).url(forResource: "claude-known-marketplaces", withExtension: "json")!

    @Test func parsesInstalledPluginsV2() throws {
        let plugins = try ClaudePluginsRegistry.installedPlugins(from: Data(contentsOf: Self.installedPluginsURL))
        let scout = plugins.first { $0.id == "scout@scout-plugin" }
        #expect(scout?.version == "0.9.0")
        #expect(scout?.installPath == "/Users/alex/.claude/plugins/cache/scout-plugin/scout/0.9.0")
        #expect(plugins.count == 2)
    }

    @Test func parsesKnownMarketplaceSources() throws {
        let markets = try ClaudePluginsRegistry.knownMarketplaces(from: Data(contentsOf: Self.knownMarketplacesURL))
        #expect(markets.first { $0.name == "scout-plugin" }?.source == .github(repo: "example-org/scout-plugin"))
        #expect(markets.first { $0.name == "example-marketplace" }?.source == .directory(path: "/Users/alex/example-marketplace"))
    }

    @Test func scoutLookupsReadFromAPluginsDir() throws {
        // scoutPlugin(pluginsDir:) / scoutMarketplace(pluginsDir:) read real
        // Claude Code file names from a directory, so stage a fresh temp dir
        // under those names from our uniquely-named bundle fixtures.
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        try Data(contentsOf: Self.installedPluginsURL).write(to: dir.appendingPathComponent("installed_plugins.json"))
        try Data(contentsOf: Self.knownMarketplacesURL).write(to: dir.appendingPathComponent("known_marketplaces.json"))

        #expect(ClaudePluginsRegistry.scoutPlugin(pluginsDir: dir)?.version == "0.9.0")
        #expect(ClaudePluginsRegistry.scoutMarketplace(pluginsDir: dir)?.installLocation == "/Users/alex/.claude/plugins/marketplaces/scout-plugin")
        #expect(ClaudePluginsRegistry.scoutPlugin(pluginsDir: URL(fileURLWithPath: "/nonexistent")) == nil)
    }

    @Test func malformedJsonThrows() {
        #expect(throws: (any Error).self) { try ClaudePluginsRegistry.installedPlugins(from: "nope".data(using: .utf8)!) }
    }
}
