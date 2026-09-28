import Foundation

/// A concrete engine on disk.
struct EngineInstall: Equatable, Sendable {
    let root: URL
    let scoutctl: URL
    let python: URL?
    let version: String?
    let vault: URL?
}

/// Who owns an engine the app did not install (spec §4.4 / §10).
enum ExternalSource: Equatable, Sendable {
    case devCheckout, installSh, claudeCode, marketplaceCache, shim
    case unknown(String)
}

enum EngineState: Equatable, Sendable {
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
    /// True when the tabs have nothing trustworthy to show (spec §5).
    var gatesTabs: Bool {
        switch self {
        case .notInstalled, .broken: return true
        case .managed(_, let bootstrapped): return !bootstrapped
        case .external: return false
        }
    }
}

/// Pure filesystem discovery, in the precedence order of spec §4.4's table.
struct EngineLocator: Sendable {
    let layout: EngineLayout
    var fileManager: FileManager = .default

    static let shimMarker = "# scout-plugin scoutctl shim"

    func pointer() -> EnginePointer? { EnginePointer.load(from: layout.pointerURL) }

    func locate() -> EngineState {
        if let p = pointer() {
            let scoutctl = URL(fileURLWithPath: p.scoutctl)
            let install = EngineInstall(root: URL(fileURLWithPath: p.engineRoot), scoutctl: scoutctl,
                                        python: URL(fileURLWithPath: p.python), version: p.version,
                                        vault: URL(fileURLWithPath: p.vault))
            guard fileManager.isExecutableFile(atPath: scoutctl.path) else {
                return .broken(install, reason: "engine pointer names a missing scoutctl: \(p.scoutctl)")
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
        guard let dest = try? fileManager.destinationOfSymbolicLink(atPath: layout.currentEngineLink.path) else { return nil }
        let root = URL(fileURLWithPath: dest, relativeTo: layout.engineDir).standardizedFileURL
        let version = root.lastPathComponent
        let scoutctl = layout.scoutctl(version: version)
        guard fileManager.isExecutableFile(atPath: scoutctl.path) else { return nil }
        return EngineInstall(root: root, scoutctl: scoutctl, python: layout.venv(version: version).appending(path: "bin/python"),
                             version: Self.version(atRoot: root) ?? version, vault: nil)
    }

    private func shimTarget() -> EngineInstall? {
        guard let text = try? String(contentsOf: layout.shimURL, encoding: .utf8),
              let target = Self.parseShimTarget(text),
              fileManager.isExecutableFile(atPath: target) else { return nil }
        let scoutctl = URL(fileURLWithPath: target)
        // <root>/.venv/bin/scoutctl → root is three levels up.
        let root = scoutctl.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
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
        return roots.lazy.compactMap { root in installIfVenv(at: root) }.first
    }

    private func devCheckout() -> EngineInstall? { installIfVenv(at: layout.devCheckout) }

    /// The pre-pointer convention: a venv at `<root>/.venv` (or `<root>/engine/.venv`).
    private func installIfVenv(at root: URL) -> EngineInstall? {
        for venv in [root.appending(path: ".venv"), root.appending(path: "engine/.venv")] {
            let scoutctl = venv.appending(path: "bin/scoutctl")
            if fileManager.isExecutableFile(atPath: scoutctl.path) {
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
