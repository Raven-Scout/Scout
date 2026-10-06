import Foundation
@testable import Scout

/// The slice of Claude Code the installer drives, simulated on a
/// `RuleBasedRunner` against a temp `~/.claude/plugins`. Nothing runs a real
/// `claude`.
///
/// - `marketplace add <dir>` requires `<dir>/.claude-plugin/marketplace.json`
///   (a directory marketplace, as Claude Code does — final review C1) and
///   records the marketplace under the manifest's own `name`. A missing or
///   invalid manifest fails the way Claude Code does, and records nothing.
/// - Like Claude Code, it CACHES the manifest it read: `marketplace add` and
///   `marketplace update` snapshot the scout version the recorded path
///   RESOLVES to (so a marketplace recorded at `engine/<old>` keeps loading
///   <old>, Ruling 69 I2), and `plugin install|update` install that cached
///   version — the marketplace entry's `version`, else the entry's own
///   `plugin.json` — which is what the register step's postcondition
///   compares. Without an `update`, an install after `current` moved gets
///   the stale cached version (final review I1). A marketplace this fake
///   never added (a test wrote `known_marketplaces.json` itself) has no
///   cache, so install reads the directory as it is now.
/// - `marketplace update` fails if the recorded manifest is gone.
///
/// `failNext` makes an exact argv fail.
final class FakeClaudeCode: @unchecked Sendable {
    let pluginsDir: URL
    private let lock = NSLock()
    private var failures: [[String]: Int] = [:]
    private var cachedScoutVersion: String?

    private var cache: String? {
        get { lock.withLock { cachedScoutVersion } }
        set { lock.withLock { cachedScoutVersion = newValue } }
    }

    init(pluginsDir: URL) { self.pluginsDir = pluginsDir }

    /// Install as a rule on `runner` for any executable named `claude`. Rules
    /// match first-come, so install this before any other `claude` rule.
    func install(on runner: RuleBasedRunner) {
        runner.on({ url, _ in url.lastPathComponent == "claude" }) { [self] _, args, _ in respond(args) }
    }

    func failNext(_ args: [String], times: Int = 1) { lock.withLock { failures[args, default: 0] += times } }

    private func takeFailure(_ args: [String]) -> Bool {
        lock.withLock {
            guard let left = failures[args], left > 0 else { return false }
            failures[args] = left - 1
            return true
        }
    }

    /// What a directory marketplace's manifest says, or why Claude Code
    /// would refuse it.
    struct Manifest { let name: String; let scoutVersion: String? }

    static func readManifest(directory: URL) -> Result<Manifest, ManifestError> {
        let file = directory.appendingPathComponent(".claude-plugin/marketplace.json")
        guard let data = try? Data(contentsOf: file) else {
            return .failure(ManifestError(message: "Marketplace file not found at \(file.path)"))
        }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let name = object["name"] as? String, !name.isEmpty,
              let plugins = object["plugins"] as? [[String: Any]] else {
            return .failure(ManifestError(message: "Invalid marketplace schema in \(file.path)"))
        }
        guard let scout = plugins.first(where: { $0["name"] as? String == "scout" }) else {
            return .success(Manifest(name: name, scoutVersion: nil))
        }
        if let version = scout["version"] as? String { return .success(Manifest(name: name, scoutVersion: version)) }
        // No version in the entry: Claude Code falls back to the plugin's own plugin.json.
        let source = (scout["source"] as? String) ?? "./"
        let root = directory.appendingPathComponent(source).standardizedFileURL
        return .success(Manifest(name: name, scoutVersion: EngineLocator.version(atRoot: root)))
    }

    struct ManifestError: Error { let message: String }

    private func respond(_ args: [String]) -> ProcessResult {
        if takeFailure(args) { return result(1, stderr: "simulated failure: \(args.joined(separator: " "))") }
        if args.starts(with: ["plugin", "marketplace", "add"]), args.count >= 4 {
            switch Self.readManifest(directory: URL(fileURLWithPath: args[3])) {
            case .failure(let error):
                return result(1, stderr: "✘ Failed to add marketplace: \(error.message)")
            case .success(let manifest):
                write([manifest.name: ["source": ["source": "directory", "path": args[3]]]], to: "known_marketplaces.json")
                cache = manifest.scoutVersion
                return result(0)
            }
        }
        if args.starts(with: ["plugin", "marketplace", "update"]) {
            guard let directory = recordedDirectory() else { return result(1, stderr: "✘ Marketplace 'scout-plugin' not found") }
            switch Self.readManifest(directory: directory.resolvingSymlinksInPath()) {
            case .failure(let error):
                return result(1, stderr: "✘ Failed to update marketplace: \(error.message)")
            case .success(let manifest):
                cache = manifest.scoutVersion
                return result(0)
            }
        }
        if args == ClaudeCodeCLI.pluginInstall || args == ClaudeCodeCLI.pluginUpdate {
            guard let directory = recordedDirectory() else { return result(1, stderr: "✘ Marketplace 'scout-plugin' not found") }
            let version: String?
            if let cached = cache {
                version = cached
            } else {
                switch Self.readManifest(directory: directory.resolvingSymlinksInPath()) {
                case .failure(let error): return result(1, stderr: "✘ Failed to install plugin: \(error.message)")
                case .success(let manifest): version = manifest.scoutVersion
                }
            }
            guard let version else { return result(1, stderr: "✘ Plugin 'scout' not found in marketplace 'scout-plugin'") }
            write(["plugins": ["scout@scout-plugin": [["scope": "user", "version": version, "installPath": directory.path]]]], to: "installed_plugins.json")
            return result(0)
        }
        return result(0)
    }

    private func recordedDirectory() -> URL? {
        guard case .directory(let path)? = ClaudePluginsRegistry.scoutMarketplace(pluginsDir: pluginsDir)?.source else { return nil }
        return URL(fileURLWithPath: path)
    }

    private func write(_ object: Any, to name: String) {
        try? FileManager.default.createDirectory(at: pluginsDir, withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: object) {
            try? data.write(to: pluginsDir.appendingPathComponent(name))
        }
    }

    private func result(_ exit: Int32, stderr: String = "") -> ProcessResult {
        ProcessResult(exitCode: exit, stdout: Data(), stderr: Data(stderr.utf8))
    }
}
