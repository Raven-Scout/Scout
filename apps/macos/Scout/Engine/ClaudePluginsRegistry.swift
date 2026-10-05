import Foundation

/// Read-only view of Claude Code's plugin bookkeeping under `~/.claude/plugins/`.
/// Shapes verified against Claude Code 2.1.259 (`installed_plugins.json`
/// schema version 2; `known_marketplaces.json` keyed by marketplace name).
/// #74's `PluginManifests` parses the same files for update checks — whichever
/// lands second should dedupe onto one type.
nonisolated struct InstalledPlugin: Equatable, Sendable {
    let id: String
    let version: String
    let installPath: String
}

nonisolated enum MarketplaceSource: Equatable, Sendable {
    case directory(path: String)
    case github(repo: String)
    case git(url: String)
    case other(String)
}

nonisolated struct KnownMarketplace: Equatable, Sendable {
    let name: String
    let source: MarketplaceSource
    let installLocation: String?
}

nonisolated enum ClaudePluginsRegistry {
    static let scoutPluginID = "scout@scout-plugin"
    static let scoutMarketplaceName = "scout-plugin"

    private struct InstalledFile: Decodable {
        struct Entry: Decodable { let version: String; let installPath: String }
        let plugins: [String: [Entry]]
    }

    private struct MarketplaceEntry: Decodable {
        struct Source: Decodable { let source: String; let path: String?; let repo: String?; let url: String? }
        let source: Source
        let installLocation: String?
    }

    static func installedPlugins(from data: Data) throws -> [InstalledPlugin] {
        let file = try JSONDecoder().decode(InstalledFile.self, from: data)
        return file.plugins.flatMap { id, entries in
            entries.map { InstalledPlugin(id: id, version: $0.version, installPath: $0.installPath) }
        }.sorted { $0.id < $1.id }
    }

    static func knownMarketplaces(from data: Data) throws -> [KnownMarketplace] {
        let file = try JSONDecoder().decode([String: MarketplaceEntry].self, from: data)
        return file.map { name, entry in
            let source: MarketplaceSource
            switch (entry.source.source, entry.source.path, entry.source.repo, entry.source.url) {
            case ("directory", let path?, _, _): source = .directory(path: path)
            case ("github", _, let repo?, _):    source = .github(repo: repo)
            case ("git", _, _, let url?):        source = .git(url: url)
            default:                             source = .other(entry.source.source)
            }
            return KnownMarketplace(name: name, source: source, installLocation: entry.installLocation)
        }.sorted { $0.name < $1.name }
    }

    static func scoutPlugin(pluginsDir: URL) -> InstalledPlugin? {
        guard let data = try? Data(contentsOf: pluginsDir.appending(path: "installed_plugins.json")),
              let plugins = try? installedPlugins(from: data) else { return nil }
        return plugins.first { $0.id == scoutPluginID }
    }

    static func scoutMarketplace(pluginsDir: URL) -> KnownMarketplace? {
        guard let data = try? Data(contentsOf: pluginsDir.appending(path: "known_marketplaces.json")),
              let markets = try? knownMarketplaces(from: data) else { return nil }
        return markets.first { $0.name == scoutMarketplaceName }
    }
}
