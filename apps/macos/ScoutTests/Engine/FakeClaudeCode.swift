import Foundation
@testable import Scout

/// The slice of Claude Code the installer drives, simulated on a
/// `RuleBasedRunner` against a temp `~/.claude/plugins`: `marketplace add`
/// records a directory marketplace; `plugin install|update` installs whatever
/// version the recorded marketplace path RESOLVES to (as Claude Code would —
/// so a marketplace recorded at `engine/<old>` keeps loading <old>, Ruling 69
/// I2); `marketplace update` is a no-op. `failNext` makes an exact argv fail.
/// Nothing runs a real `claude`.
final class FakeClaudeCode: @unchecked Sendable {
    let pluginsDir: URL
    private let lock = NSLock()
    private var failures: [[String]: Int] = [:]

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

    private func respond(_ args: [String]) -> ProcessResult {
        if takeFailure(args) { return result(1, stderr: "simulated failure: \(args.joined(separator: " "))") }
        if args.starts(with: ["plugin", "marketplace", "add"]), args.count >= 4 {
            write(["scout-plugin": ["source": ["source": "directory", "path": args[3]]]], to: "known_marketplaces.json")
            return result(0)
        }
        if args.starts(with: ["plugin", "marketplace", "update"]) { return result(0) }
        if args == ClaudeCodeCLI.pluginInstall || args == ClaudeCodeCLI.pluginUpdate {
            guard case .directory(let path)? = ClaudePluginsRegistry.scoutMarketplace(pluginsDir: pluginsDir)?.source,
                  let version = EngineLocator.version(atRoot: URL(fileURLWithPath: path).resolvingSymlinksInPath()) else {
                return result(1, stderr: "marketplace not found")
            }
            write(["plugins": ["scout@scout-plugin": [["version": version, "installPath": path]]]], to: "installed_plugins.json")
            return result(0)
        }
        return result(0)
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
