import Foundation

/// A concrete engine on disk.
nonisolated struct EngineInstall: Equatable, Sendable {
    let root: URL
    let scoutctl: URL
    let python: URL?
    let version: String?
    let vault: URL?
    /// The pointer's `managed_by`, carried only on a `.broken` pointer so the
    /// app can tell its own broken install (offer Repair) from someone
    /// else's (hand off, never modify — Ruling 69 I6). nil everywhere else.
    var managedBy: String? = nil
}

/// Who owns an engine the app did not install (spec §4.4 / §10).
nonisolated enum ExternalSource: Equatable, Sendable {
    case devCheckout, installSh, claudeCode, marketplaceCache, shim
    case unknown(String)
}

nonisolated enum EngineState: Equatable, Sendable {
    case notInstalled
    case managed(EngineInstall, vaultBootstrapped: Bool)
    case external(EngineInstall, ExternalSource)
    case broken(EngineInstall?, reason: String)

    var install: EngineInstall? {
        switch self {
        case .managed(let i, _), .external(let i, _): return i
        case .broken(let i, _): return i
        case .notInstalled: return nil
        }
    }
    var scoutctl: URL? { install?.scoutctl }
    var isManaged: Bool { if case .managed = self { return true }; return false }
    /// A broken pointer some other installer wrote (`managed_by` ≠
    /// `scout-app`): an external engine the app must never modify (spec
    /// §10), so it gets the `/scout-update` hand-off — no onboarding gate,
    /// no Set up / Repair (Ruling 69 I6).
    var isBrokenOutsideApp: Bool {
        if case .broken(let install?, _) = self, let owner = install.managedBy, owner != "scout-app" { return true }
        return false
    }
    /// True when the tabs have nothing trustworthy to show and the app can
    /// set the engine up itself (spec §5) — the onboarding gate.
    var gatesTabs: Bool {
        switch self {
        case .notInstalled: return true
        case .broken: return !isBrokenOutsideApp
        case .managed(_, let bootstrapped): return !bootstrapped
        case .external: return false
        }
    }
}

/// Pure filesystem discovery, in the precedence order of spec §4.4's table.
nonisolated struct EngineLocator: Sendable {
    let layout: EngineLayout

    static let shimMarker = "# scout-plugin scoutctl shim"

    func pointer() -> EnginePointer? { EnginePointer.load(from: layout.pointerURL) }

    func locate() -> EngineState {
        if let p = pointer() {
            let scoutctl = URL(fileURLWithPath: p.scoutctl)
            let install = EngineInstall(root: URL(fileURLWithPath: p.engineRoot), scoutctl: scoutctl,
                                        python: URL(fileURLWithPath: p.python), version: p.version,
                                        vault: URL(fileURLWithPath: p.vault))
            guard FileManager.default.isExecutableFile(atPath: scoutctl.path) else {
                let owned = EngineInstall(root: install.root, scoutctl: scoutctl, python: install.python, version: p.version,
                                          vault: install.vault, managedBy: p.managedBy)
                return .broken(owned, reason: "engine pointer names a missing scoutctl: \(p.scoutctl)")
            }
            return p.managedBy == "scout-app"
                ? .managed(install, vaultBootstrapped: true)
                : .external(install, Self.externalSource(managedBy: p.managedBy))
        }
        if let conventional = conventionalLayout() { return .managed(conventional, vaultBootstrapped: false) }
        if let shim = shimTarget() { return .external(shim, .shim) }
        if let cache = marketplaceCacheInstall() { return .external(cache, .marketplaceCache) }
        if let dev = devCheckout() { return .external(dev, .devCheckout) }
        return .notInstalled
    }

    // MARK: discovery helpers

    /// `engine/current` → versioned root; venv beside it. The installer stopped
    /// before `bootstrap` (which writes the pointer), or a user deleted state.
    private func conventionalLayout() -> EngineInstall? {
        guard let dest = try? FileManager.default.destinationOfSymbolicLink(atPath: layout.currentEngineLink.path) else { return nil }
        // `URL(fileURLWithPath:relativeTo:)` treats a non-directory base (no
        // trailing slash) as a FILE, so the relative component REPLACES its
        // last path segment instead of being appended under it — the same
        // bug `EngineInstaller.repointCurrent` had (Ruling 54). `current`'s
        // target is normally absolute (the installer always writes one), but
        // tolerate a relative one by resolving it against the engine
        // directory the way `appending(path:)` does everywhere else.
        let root = (dest.hasPrefix("/") ? URL(fileURLWithPath: dest) : layout.engineDir.appending(path: dest)).standardizedFileURL
        let version = root.lastPathComponent
        let scoutctl = layout.scoutctl(version: version)
        guard FileManager.default.isExecutableFile(atPath: scoutctl.path) else { return nil }
        return EngineInstall(root: root, scoutctl: scoutctl, python: layout.venv(version: version).appending(path: "bin/python"),
                             version: Self.version(atRoot: root) ?? version, vault: nil)
    }

    private func shimTarget() -> EngineInstall? {
        guard let text = try? String(contentsOf: layout.shimURL, encoding: .utf8),
              let target = Self.parseShimTarget(text),
              FileManager.default.isExecutableFile(atPath: target) else { return nil }
        let scoutctl = URL(fileURLWithPath: target)
        // <root>/.venv/bin/scoutctl → root is three levels up. The other
        // layout `installIfVenv` accepts, <root>/engine/.venv/bin/scoutctl,
        // lands on <root>/engine — which has no plugin manifest — so step up
        // once more to the plugin root.
        var root = scoutctl.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        if Self.version(atRoot: root) == nil && root.lastPathComponent == "engine" {
            root = root.deletingLastPathComponent()
        }
        // A shim into a Raven-Scout/Scout monorepo's own root (`.venv` or
        // `engine/.venv`) names the stale pre-monorepo venv that
        // `installIfCheckout` refuses to adopt; the plugin and its venv live
        // under `plugin/`. Don't adopt it here either: fall through, so the
        // dev-checkout candidate picks up `plugin/` when it has a venv.
        if FileManager.default.fileExists(atPath: EngineLayout.monorepoPlugin(in: root).appending(path: ".claude-plugin/plugin.json").path) {
            return nil
        }
        return EngineInstall(root: root, scoutctl: scoutctl, python: scoutctl.deletingLastPathComponent().appending(path: "python"),
                             version: Self.version(atRoot: root), vault: nil)
    }

    private func marketplaceCacheInstall() -> EngineInstall? {
        var roots: [URL] = []
        if let plugin = ClaudePluginsRegistry.scoutPlugin(pluginsDir: layout.claudePluginsDir) {
            roots.append(URL(fileURLWithPath: plugin.installPath))
        }
        if let loc = ClaudePluginsRegistry.scoutMarketplace(pluginsDir: layout.claudePluginsDir)?.installLocation {
            roots.append(URL(fileURLWithPath: loc))
        }
        return roots.lazy.compactMap { root in installIfCheckout(at: root) }.first
    }

    private func devCheckout() -> EngineInstall? { installIfCheckout(at: layout.devCheckout) }

    /// A checkout or clone in either repo shape. A Raven-Scout/Scout monorepo
    /// (`plugin/.claude-plugin/plugin.json` exists) keeps its venv under
    /// `plugin/`. Any venv at the monorepo's own root is stale, left from
    /// before the clone was pulled into the monorepo, and is never adopted.
    /// The engine's `bin/scoutctl` launcher also looks under `plugin/` for a
    /// monorepo marketplace clone. Anything else is a legacy scout-plugin
    /// tree with the plugin at its root.
    private func installIfCheckout(at root: URL) -> EngineInstall? {
        let plugin = EngineLayout.monorepoPlugin(in: root)
        if FileManager.default.fileExists(atPath: plugin.appending(path: ".claude-plugin/plugin.json").path) {
            return installIfVenv(at: plugin)
        }
        return installIfVenv(at: root)
    }

    /// The pre-pointer convention: a venv at `<root>/.venv` (or `<root>/engine/.venv`).
    private func installIfVenv(at root: URL) -> EngineInstall? {
        for venv in [root.appending(path: ".venv"), root.appending(path: "engine/.venv")] {
            let scoutctl = venv.appending(path: "bin/scoutctl")
            if FileManager.default.isExecutableFile(atPath: scoutctl.path) {
                return EngineInstall(root: root, scoutctl: scoutctl, python: venv.appending(path: "bin/python"),
                                     version: Self.version(atRoot: root), vault: nil)
            }
        }
        return nil
    }

    // MARK: pure helpers

    static func version(atRoot root: URL) -> String? {
        struct Manifest: Decodable { let version: String }
        guard let data = try? Data(contentsOf: root.appending(path: ".claude-plugin/plugin.json")) else { return nil }
        return try? JSONDecoder().decode(Manifest.self, from: data).version
    }

    static func parseShimTarget(_ text: String) -> String? {
        guard text.contains(shimMarker),
              let range = text.range(of: #"exec "([^"]+)""#, options: .regularExpression) else { return nil }
        let match = text[range]
        guard let open = match.firstIndex(of: "\""), let close = match.lastIndex(of: "\""), open < close else { return nil }
        return String(match[match.index(after: open)..<close])
    }

    static func externalSource(managedBy: String) -> ExternalSource {
        switch managedBy {
        case "dev": return .devCheckout
        case "install.sh": return .installSh
        case "claude-code": return .claudeCode
        default: return .unknown(managedBy)
        }
    }
}
