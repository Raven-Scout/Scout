import Testing
import Foundation
@testable import Scout

/// Records fetcher calls so a test can prove the fetcher was (or wasn't)
/// invoked, without racing a plain array across concurrency domains.
private actor CallRecorder {
    private(set) var urls: [URL] = []
    func record(_ url: URL) { urls.append(url) }
}

private struct StubFetcher: RemoteDataFetcher {
    var responses: [URL: Data] = [:]
    var error: Error?
    var recorder: CallRecorder?

    func data(from url: URL) async throws -> Data {
        await recorder?.record(url)
        if let error { throw error }
        guard let data = responses[url] else { throw URLError(.fileDoesNotExist) }
        return data
    }
}

@Suite("PluginUpdateChecker")
struct PluginUpdateCheckerTests {
    private let rawManifestURL = URL(string: "https://raw.githubusercontent.com/example-org/scout-plugin/HEAD/.claude-plugin/marketplace.json")!

    private let githubMarketplace = #"""
    {"scout-plugin":{"source":{"source":"github","repo":"example-org/scout-plugin"},"installLocation":"/Users/alex/.claude/plugins/marketplaces/scout-plugin"}}
    """#
    private let gitMarketplace = #"""
    {"scout-plugin":{"source":{"source":"git","url":"https://github.com/example-org/scout-plugin.git"},"installLocation":"/Users/alex/.claude/plugins/marketplaces/scout-plugin"}}
    """#

    private func tempDir() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("puc-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    /// A `pluginsDir` modelled on `ScoutTests/Fixtures/claude-plugins/`, with
    /// an optional `installed_plugins.json` entry (nil to model Claude Code
    /// not knowing about the plugin) and the given `known_marketplaces.json`
    /// body.
    private func pluginsDir(installedVersion: String? = "0.7.2", marketplace: String?) throws -> URL {
        let dir = try tempDir()
        if let installedVersion {
            try Data(#"""
            {"version":2,"plugins":{"scout@scout-plugin":[{"scope":"user","installPath":"/Users/alex/.claude/plugins/cache/scout-plugin/scout/\#(installedVersion)","version":"\#(installedVersion)"}]}}
            """#.utf8).write(to: dir.appendingPathComponent("installed_plugins.json"))
        }
        if let marketplace {
            try Data(marketplace.utf8).write(to: dir.appendingPathComponent("known_marketplaces.json"))
        }
        return dir
    }

    private func externalEngine(root: URL = URL(fileURLWithPath: "/Users/alex/scout-plugin"), version: String? = "0.7.2") -> EngineState {
        .external(EngineInstall(root: root, scoutctl: root.appendingPathComponent(".venv/bin/scoutctl"),
                                 python: nil, version: version, vault: nil), .devCheckout)
    }

    private func manifest(_ version: String) -> Data { Data(#"{"plugins":[{"name":"scout","version":"\#(version)"}]}"#.utf8) }

    // MARK: not applicable

    @Test func managedEngineIsNotApplicableAndNeverFetches() async throws {
        let recorder = CallRecorder()
        let checker = PluginUpdateChecker(pluginsDir: try tempDir(), fetcher: StubFetcher(recorder: recorder))
        let install = EngineInstall(root: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/0.1.0"),
                                     scoutctl: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/0.1.0/.venv/bin/scoutctl"),
                                     python: nil, version: "0.1.0", vault: nil)
        let result = await checker.check(engine: .managed(install, vaultBootstrapped: true))
        #expect(result == .notApplicable)
        #expect(await recorder.urls.isEmpty)
    }

    @Test func notInstalledIsNotApplicableAndNeverFetches() async throws {
        let recorder = CallRecorder()
        let checker = PluginUpdateChecker(pluginsDir: try tempDir(), fetcher: StubFetcher(recorder: recorder))
        let result = await checker.check(engine: .notInstalled)
        #expect(result == .notApplicable)
        #expect(await recorder.urls.isEmpty)
    }

    @Test func brokenEngineIsNotApplicableAndNeverFetches() async throws {
        let recorder = CallRecorder()
        let checker = PluginUpdateChecker(pluginsDir: try tempDir(), fetcher: StubFetcher(recorder: recorder))
        let result = await checker.check(engine: .broken(nil, reason: "missing scoutctl"))
        #expect(result == .notApplicable)
        #expect(await recorder.urls.isEmpty)
    }

    // MARK: github / git sources

    @Test func githubSourceReportsAvailableWhenRemoteIsNewer() async throws {
        let dir = try pluginsDir(marketplace: githubMarketplace)
        let checker = PluginUpdateChecker(pluginsDir: dir, fetcher: StubFetcher(responses: [rawManifestURL: manifest("0.8.0")]))
        let result = await checker.check(engine: externalEngine())
        #expect(result.applicable)
        #expect(result.installed == "0.7.2")
        #expect(result.latest == "0.8.0")
        #expect(result.isUpdateAvailable)
        #expect(result.releasesURL == URL(string: "https://github.com/example-org/scout-plugin/releases"))
        #expect(result.error == nil)
    }

    @Test func githubSourceUpToDate() async throws {
        let dir = try pluginsDir(marketplace: githubMarketplace)
        let checker = PluginUpdateChecker(pluginsDir: dir, fetcher: StubFetcher(responses: [rawManifestURL: manifest("0.7.2")]))
        let result = await checker.check(engine: externalEngine())
        #expect(result.isUpdateAvailable == false)
        #expect(result.latest == "0.7.2")
        #expect(result.error == nil)
    }

    @Test func gitSourceUsesTheSameRawURL() async throws {
        let dir = try pluginsDir(marketplace: gitMarketplace)
        let checker = PluginUpdateChecker(pluginsDir: dir, fetcher: StubFetcher(responses: [rawManifestURL: manifest("0.9.0")]))
        let result = await checker.check(engine: externalEngine())
        #expect(result.latest == "0.9.0")
        #expect(result.isUpdateAvailable)
        #expect(result.releasesURL == URL(string: "https://github.com/example-org/scout-plugin/releases"))
    }

    // MARK: directory source

    @Test func directorySourceReadsWorkingCopyAndNeverFetches() async throws {
        let pluginRoot = try tempDir()
        try FileManager.default.createDirectory(at: pluginRoot.appendingPathComponent(".claude-plugin"), withIntermediateDirectories: true)
        try manifest("0.8.1").write(to: pluginRoot.appendingPathComponent(".claude-plugin/marketplace.json"))

        let marketplace = #"{"scout-plugin":{"source":{"source":"directory","path":"\#(pluginRoot.path)"},"installLocation":"\#(pluginRoot.path)"}}"#
        let dir = try pluginsDir(marketplace: marketplace)
        let recorder = CallRecorder()
        let checker = PluginUpdateChecker(pluginsDir: dir, fetcher: StubFetcher(recorder: recorder))

        let result = await checker.check(engine: externalEngine())
        #expect(result.latest == "0.8.1")
        #expect(result.isUpdateAvailable)
        #expect(result.releasesURL == nil)
        #expect(result.error == nil)
        #expect(await recorder.urls.isEmpty)
    }

    // MARK: failures

    @Test func networkFailureIsAnErrorNotAFalseUpToDate() async throws {
        let dir = try pluginsDir(marketplace: githubMarketplace)
        let checker = PluginUpdateChecker(pluginsDir: dir, fetcher: StubFetcher(error: URLError(.notConnectedToInternet)))
        let result = await checker.check(engine: externalEngine())
        #expect(result.installed == "0.7.2")
        #expect(result.latest == nil)
        #expect(result.isUpdateAvailable == false)
        #expect(result.error != nil)
    }

    @Test func remoteManifestWithNoScoutEntryIsAnError() async throws {
        let dir = try pluginsDir(marketplace: githubMarketplace)
        let noScoutEntry = Data(#"{"plugins":[{"name":"other","version":"1.0.0"}]}"#.utf8)
        let checker = PluginUpdateChecker(pluginsDir: dir, fetcher: StubFetcher(responses: [rawManifestURL: noScoutEntry]))
        let result = await checker.check(engine: externalEngine())
        #expect(result.latest == nil)
        #expect(result.isUpdateAvailable == false)
        #expect(result.error != nil)
    }

    @Test func unparsableRemoteVersionIsAnErrorContainingLatest() async throws {
        let dir = try pluginsDir(marketplace: githubMarketplace)
        let checker = PluginUpdateChecker(pluginsDir: dir, fetcher: StubFetcher(responses: [rawManifestURL: manifest("latest")]))
        let result = await checker.check(engine: externalEngine())
        #expect(result.isUpdateAvailable == false)
        #expect(result.error?.contains("latest") == true)
    }

    // MARK: installed-version fallback

    @Test func noInstalledPluginsEntryFallsBackToInstallVersion() async throws {
        let dir = try pluginsDir(installedVersion: nil, marketplace: githubMarketplace)
        let checker = PluginUpdateChecker(pluginsDir: dir, fetcher: StubFetcher(responses: [rawManifestURL: manifest("0.8.0")]))
        let result = await checker.check(engine: externalEngine(version: "0.7.0"))
        #expect(result.installed == "0.7.0")
        #expect(result.isUpdateAvailable)
        #expect(result.error == nil)
    }

    @Test func neitherInstalledPluginsNorInstallVersionIsAnError() async throws {
        let dir = try pluginsDir(installedVersion: nil, marketplace: githubMarketplace)
        let checker = PluginUpdateChecker(pluginsDir: dir, fetcher: StubFetcher(responses: [rawManifestURL: manifest("0.8.0")]))
        let result = await checker.check(engine: externalEngine(version: nil))
        #expect(result.applicable)
        #expect(result.installed == nil)
        #expect(result.error != nil)
    }

    @Test func missingMarketplaceEntryIsAnError() async throws {
        let dir = try pluginsDir(marketplace: nil)
        let checker = PluginUpdateChecker(pluginsDir: dir, fetcher: StubFetcher())
        let result = await checker.check(engine: externalEngine())
        #expect(result.installed == "0.7.2")
        #expect(result.error != nil)
    }
}
