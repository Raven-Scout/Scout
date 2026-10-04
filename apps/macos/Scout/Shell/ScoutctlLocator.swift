import Foundation

/// Resolves where `scoutctl` lives on this machine.
///
/// Extracted from `AppState.resolveScoutctlPath()` so it can be tested without
/// touching the real filesystem — the previous inline version carried a
/// first-priority candidate (`~/scout-plugin/bin/scoutctl`) that has never
/// existed, and no test could see it. The executable is at
/// `engine/bin/scoutctl`.
///
/// Priority order, most authoritative first:
///  1. The installed plugin cache, whose path is recorded in
///     `~/.claude/plugins/installed_plugins.json`. This is where a normal
///     (non-developer) user's scoutctl actually is.
///  2. A developer checkout at `~/scout-plugin/engine/bin/scoutctl`.
///  3. pip/pipx/homebrew install locations.
///  4. `/usr/bin/env scoutctl`, leaning on `$PATH`.
enum ScoutctlLocator {
    /// Key under `plugins` in installed_plugins.json.
    private static let pluginKey = "scout@scout-plugin"

    /// Extract the installed plugin's `installPath` from the manifest JSON.
    /// Returns nil for absent, malformed, or empty manifests.
    static func installedPluginPath(json: String?) -> String? {
        guard let json,
              let data = json.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let plugins = root["plugins"] as? [String: Any],
              let entries = plugins[pluginKey] as? [[String: Any]],
              let first = entries.first,
              let path = first["installPath"] as? String,
              !path.isEmpty
        else { return nil }
        return path
    }

    /// Extract the installed plugin's version from the manifest JSON.
    static func installedPluginVersion(json: String?) -> String? {
        guard let json,
              let data = json.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let plugins = root["plugins"] as? [String: Any],
              let entries = plugins[pluginKey] as? [[String: Any]],
              let version = entries.first?["version"] as? String,
              !version.isEmpty
        else { return nil }
        return version
    }

    /// Default location of the plugin manifest.
    static func installedPluginsJSONURL(home: URL) -> URL {
        home.appendingPathComponent(".claude/plugins/installed_plugins.json")
    }

    static func resolve(
        home: URL,
        installedPluginsJSON: String?,
        isExecutable: (URL) -> Bool
    ) -> AppState.ScoutctlInvocation {
        var candidates: [URL] = []

        if let installed = installedPluginPath(json: installedPluginsJSON) {
            candidates.append(
                URL(fileURLWithPath: installed).appendingPathComponent("engine/bin/scoutctl")
            )
        }
        candidates += [
            home.appendingPathComponent("scout-plugin/engine/bin/scoutctl"),
            home.appendingPathComponent("miniconda3/bin/scoutctl"),
            home.appendingPathComponent(".local/bin/scoutctl"),
            URL(fileURLWithPath: "/opt/homebrew/bin/scoutctl"),
            URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
        ]

        for url in candidates where isExecutable(url) {
            return AppState.ScoutctlInvocation(executable: url, argsPrefix: [])
        }
        return AppState.ScoutctlInvocation(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            argsPrefix: ["scoutctl"]
        )
    }
}
