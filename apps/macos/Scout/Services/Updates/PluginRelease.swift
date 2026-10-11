import Foundation

/// Pure reader for the Scout plugin's version in a Claude Code marketplace
/// manifest (`.claude-plugin/marketplace.json`), plus URL builders for
/// locating that manifest and linking to releases. Takes `Data` so tests use
/// fixtures, never a real vault. Lenient JSON (`JSONSerialization`) — unknown
/// keys are fine, anything malformed yields nil rather than a crash.
///
/// `PluginUpdateChecker` (Task 6) uses this to find "latest" for engines the
/// app doesn't manage. The installed side (`installed_plugins.json`,
/// `known_marketplaces.json`, `MarketplaceSource`) already lives in
/// `ClaudePluginsRegistry` — this type only adds the manifest-reading half.
nonisolated enum PluginRelease {
    static let marketplaceManifestPath = ".claude-plugin/marketplace.json"
    static let pluginName = "scout"

    /// `plugins[name == "scout"].version` from a marketplace.json; nil if
    /// absent, malformed, or empty.
    static func scoutVersion(fromMarketplaceManifest data: Data) -> String? {
        guard let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let plugins = root["plugins"] as? [[String: Any]],
              let entry = plugins.first(where: { ($0["name"] as? String) == pluginName }),
              let version = entry["version"] as? String, !version.isEmpty
        else { return nil }
        return version
    }

    /// When the marketplace's scout entry pins a GitHub-hosted git source
    /// (`github`, `url` or `git-subdir`, as the root manifest does since
    /// #362: `git-subdir` with `"ref": "stable"`), the raw `plugin.json` at
    /// that ref: Claude Code installs that tree and takes its version from
    /// that `plugin.json`, not from the entry's `version`. The `sha`
    /// outranks the `ref` and no ref means the default branch, as in Claude
    /// Code. nil for a relative-path source, a non-GitHub host, or anything
    /// malformed; the caller then uses the entry's version.
    static func pinnedPluginManifestURL(fromMarketplaceManifest data: Data) -> URL? {
        guard let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let plugins = root["plugins"] as? [[String: Any]],
              let entry = plugins.first(where: { ($0["name"] as? String) == pluginName }),
              let source = entry["source"] as? [String: Any]
        else { return nil }
        let repo: String?
        switch source["source"] as? String {
        case "github":
            repo = source["repo"] as? String
        case "url":
            repo = (source["url"] as? String).flatMap(githubRepo(fromGitURL:))
        case "git-subdir":
            guard let url = source["url"] as? String else { return nil }
            repo = githubRepo(fromGitURL: url) ?? (url.contains(":") ? nil : url)
        default:
            return nil
        }
        guard let repo, repo.split(separator: "/").count == 2 else { return nil }
        let ref = (source["sha"] as? String) ?? (source["ref"] as? String) ?? "HEAD"
        let path = ((source["path"] as? String) ?? "").split(separator: "/").joined(separator: "/")
        let manifest = path.isEmpty ? ".claude-plugin/plugin.json" : "\(path)/.claude-plugin/plugin.json"
        return URL(string: "https://raw.githubusercontent.com/\(repo)/\(ref)/\(manifest)")
    }

    /// `version` from a `plugin.json`; nil if absent, malformed, or empty.
    static func version(fromPluginManifest data: Data) -> String? {
        guard let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let version = root["version"] as? String, !version.isEmpty
        else { return nil }
        return version
    }

    /// Where the "latest" manifest lives for a source. GitHub-hosted sources
    /// read the raw manifest at `HEAD` (the default branch) — no API call
    /// needed. Directory sources (dev checkouts) read the working copy.
    /// Anything else (a non-GitHub git remote, `.other`) → nil.
    static func latestManifestURL(for source: MarketplaceSource) -> URL? {
        switch source {
        case .github(let repo):
            return URL(string: "https://raw.githubusercontent.com/\(repo)/HEAD/\(marketplaceManifestPath)")
        case .git(let url):
            guard let repo = githubRepo(fromGitURL: url) else { return nil }
            return URL(string: "https://raw.githubusercontent.com/\(repo)/HEAD/\(marketplaceManifestPath)")
        case .directory(let path):
            return URL(fileURLWithPath: path).appendingPathComponent(marketplaceManifestPath)
        case .other:
            return nil
        }
    }

    /// GitHub's releases page for a source; nil for anything not hosted on
    /// GitHub (a directory checkout has no releases page to link to).
    static func releasesURL(for source: MarketplaceSource) -> URL? {
        switch source {
        case .github(let repo):
            return URL(string: "https://github.com/\(repo)/releases")
        case .git(let url):
            guard let repo = githubRepo(fromGitURL: url) else { return nil }
            return URL(string: "https://github.com/\(repo)/releases")
        case .directory, .other:
            return nil
        }
    }

    /// `https://github.com/o/r(.git)` or `git@github.com:o/r(.git)` → `o/r`;
    /// anything else (a different host, a malformed path) → nil.
    static func githubRepo(fromGitURL url: String) -> String? {
        var path: Substring
        if url.hasPrefix("git@github.com:") {
            path = url.dropFirst("git@github.com:".count)
        } else if let components = URLComponents(string: url),
                  components.host?.lowercased() == "github.com" {
            path = Substring(components.path.drop(while: { $0 == "/" }))
        } else {
            return nil
        }
        if path.hasSuffix(".git") { path = path.dropLast(4) }
        let parts = path.split(separator: "/")
        guard parts.count == 2, !parts[0].isEmpty, !parts[1].isEmpty else { return nil }
        return "\(parts[0])/\(parts[1])"
    }
}
