import Foundation

/// One HTTPS GET, injectable so tests never touch the network. Copied
/// verbatim from the older plan's Task 7 (see `old-plan-task-7.md`).
nonisolated protocol RemoteDataFetcher: Sendable {
    func data(from url: URL) async throws -> Data
}

nonisolated struct URLSessionFetcher: RemoteDataFetcher {
    private let session: URLSession

    init() {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 10
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(configuration: config)
    }

    func data(from url: URL) async throws -> Data {
        let (data, response) = try await session.data(from: url)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw URLError(.badServerResponse, userInfo: [NSLocalizedDescriptionKey: "HTTP \(http.statusCode) from \(url.host ?? url.absoluteString)"])
        }
        return data
    }
}

/// Plugin track: is the installed Claude Code plugin behind the marketplace
/// it was installed from? Only applicable to externally-managed engines
/// (`.external`) — a Scout-managed engine updates its own plugin, and there
/// is nothing to compare for `.notInstalled`/`.broken`.
///
/// Installed version: `ClaudePluginsRegistry.scoutPlugin(pluginsDir:)`,
/// falling back to the engine install's own `version` when Claude Code has no
/// `installed_plugins.json` entry for it. Latest version: source-aware, via
/// `PluginRelease` — the marketplace's working copy on disk for a `directory`
/// source (dev checkouts), the raw GitHub manifest at `HEAD` for `github`/
/// `git` sources (end users). Never throws; every failure becomes
/// `PluginUpdateResult.error` and is shown only in Settings ▸ Updates.
nonisolated struct PluginUpdateChecker: PluginUpdateChecking {
    let pluginsDir: URL
    let fetcher: any RemoteDataFetcher

    /// The real `~/.claude/plugins` layout.
    static func standard(fetcher: any RemoteDataFetcher = URLSessionFetcher()) -> PluginUpdateChecker {
        PluginUpdateChecker(pluginsDir: EngineLayout.live.claudePluginsDir, fetcher: fetcher)
    }

    func check(engine: EngineState) async -> PluginUpdateResult {
        guard case .external(let install, _) = engine else { return .notApplicable }

        guard let installed = ClaudePluginsRegistry.scoutPlugin(pluginsDir: pluginsDir)?.version ?? install.version else {
            return PluginUpdateResult(applicable: true, installed: nil, latest: nil, isUpdateAvailable: false,
                                       releasesURL: nil, error: "Couldn't tell which plugin version is installed.")
        }

        guard let marketplace = ClaudePluginsRegistry.scoutMarketplace(pluginsDir: pluginsDir) else {
            return PluginUpdateResult(applicable: true, installed: installed, latest: nil, isUpdateAvailable: false,
                                       releasesURL: nil, error: "Couldn't read the scout-plugin marketplace entry.")
        }
        let releasesURL = PluginRelease.releasesURL(for: marketplace.source)

        guard let manifestURL = PluginRelease.latestManifestURL(for: marketplace.source) else {
            return PluginUpdateResult(applicable: true, installed: installed, latest: nil, isUpdateAvailable: false,
                                       releasesURL: releasesURL, error: "Unsupported plugin source.")
        }

        let manifestData: Data
        do {
            manifestData = manifestURL.isFileURL ? try Data(contentsOf: manifestURL) : try await fetcher.data(from: manifestURL)
        } catch {
            return PluginUpdateResult(applicable: true, installed: installed, latest: nil, isUpdateAvailable: false,
                                       releasesURL: releasesURL, error: "Couldn't check the latest plugin version: \(error.localizedDescription)")
        }

        guard let latest = PluginRelease.scoutVersion(fromMarketplaceManifest: manifestData) else {
            return PluginUpdateResult(applicable: true, installed: installed, latest: nil, isUpdateAvailable: false,
                                       releasesURL: releasesURL, error: "The latest plugin manifest has no scout version.")
        }

        guard let installedVersion = EngineVersion(installed), let latestVersion = EngineVersion(latest) else {
            return PluginUpdateResult(applicable: true, installed: installed, latest: latest, isUpdateAvailable: false,
                                       releasesURL: releasesURL, error: "Couldn't compare plugin versions (\(installed) vs \(latest)).")
        }

        return PluginUpdateResult(applicable: true, installed: installed, latest: latest,
                                   isUpdateAvailable: latestVersion > installedVersion,
                                   releasesURL: releasesURL, error: nil)
    }
}
