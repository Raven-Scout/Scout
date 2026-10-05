import Foundation

/// Where an app-managed engine lives (spec §4.1). Every path derives from
/// `home` so tests point it at a temp directory. Mirrors scout-plugin's
/// `engine_pointer.py` and the `~/.local/{share,state,bin}` conventions Claude
/// Code itself uses (`~/.local/bin/claude`, `~/.local/share/claude/versions/`).
nonisolated struct EngineLayout: Equatable, Sendable {
    let home: URL

    var shareDir: URL { home.appending(path: ".local/share/scout") }
    var engineDir: URL { shareDir.appending(path: "engine") }
    var venvDir: URL { shareDir.appending(path: "venv") }
    var currentEngineLink: URL { engineDir.appending(path: "current") }
    var stateDir: URL { home.appending(path: ".local/state/scout") }
    var pointerURL: URL { stateDir.appending(path: "engine.json") }
    var installLogURL: URL { stateDir.appending(path: "install.log") }
    var localBin: URL { home.appending(path: ".local/bin") }
    /// The shim `scoutctl bootstrap` writes; also the placeholder executable
    /// AppState hands services when no engine is found (ENOENT → clear error).
    var shimURL: URL { localBin.appending(path: "scoutctl") }
    var uvURL: URL { localBin.appending(path: "uv") }
    var claudePluginsDir: URL { home.appending(path: ".claude/plugins") }
    /// The maintainer's dev checkout; adopted read-only (spec §10).
    ///
    /// The same clone serves both repo shapes. Before the 2026-10-05
    /// monorepo merge it was a scout-plugin checkout with the plugin at its
    /// root. After it, the repo is Raven-Scout/Scout (formerly
    /// scout-plugin), and pulling the existing clone leaves the plugin under
    /// `plugin/` (`monorepoPlugin(in:)`). The cutover runbook keeps this
    /// path and does not add a second default location, so neither does
    /// the app. A monorepo clone anywhere else is found through the
    /// pointer or the shim, which `scoutctl bootstrap upgrade --managed-by
    /// dev` writes.
    var devCheckout: URL { home.appending(path: "scout-plugin") }

    /// Where a Raven-Scout/Scout monorepo checkout or marketplace clone keeps
    /// the plugin: `<checkout>/plugin`, marked by
    /// `plugin/.claude-plugin/plugin.json`.
    static func monorepoPlugin(in checkout: URL) -> URL { checkout.appending(path: "plugin") }

    func engineRoot(version: String) -> URL { engineDir.appending(path: version) }
    func venv(version: String) -> URL { venvDir.appending(path: version) }
    func scoutctl(version: String) -> URL { venv(version: version).appending(path: "bin/scoutctl") }

    static let live = EngineLayout(home: FileManager.default.homeDirectoryForCurrentUser)
}
