import Foundation

/// When and how the Sessions page rebuilds the index (spec §3, addendum §10).
///
/// Two lanes, each single-flight:
/// - **fast** — `session index --json --no-gh`: on source-file events (at most
///   one build per `eventWindow` while events keep arriving), on a heartbeat,
///   and after every PR build. PR state comes from the engine's PR cache.
/// - **PR** — `session index --json` with `gh`: every `prInterval` while the
///   app runs and when the page appears. Up to ~30 s while PRs are due, so it
///   never blocks the fast lane, and its index is never published: by the time
///   it is written its liveness is that old. It only refreshes the PR cache.
nonisolated enum SessionsRefresh {

    struct Intervals: Equatable, Sendable {
        /// Window that coalesces file events into one build.
        var eventWindow: Duration
        /// Fast build while the page is visible, so `running` (which expires
        /// 120 s after the last activity with no file event to announce it)
        /// and relative ages stay current.
        var visibleHeartbeat: Duration
        /// Fast build while the page is hidden, for the sidebar badge.
        var hiddenHeartbeat: Duration
        /// PR build cadence. The engine's 10-minute TTL decides what is
        /// actually fetched, so a run with nothing due costs a fast build.
        var prInterval: Duration

        static let production = Intervals(
            eventWindow: .seconds(2),
            visibleHeartbeat: .seconds(30),
            hiddenHeartbeat: .seconds(300),
            prInterval: .seconds(120)
        )
    }

    static func fastArguments(prefix: [String]) -> [String] {
        prefix + ["session", "index", "--json", "--no-gh"]
    }

    static func prArguments(prefix: [String]) -> [String] {
        prefix + ["session", "index", "--json"]
    }

    /// The directories whose changes can change the index. Not the Claude
    /// Application Support root — the desktop app writes caches and logs there
    /// constantly — and not the vault's `.scout-cache/`, which holds only the
    /// engine's own output (group and lease changes arrive on the heartbeat).
    static func watchRoots(claudeHome: URL, desktopSupport: URL) -> [URL] {
        [
            desktopSupport.appendingPathComponent("claude-code-sessions", isDirectory: true),
            claudeHome.appendingPathComponent("sessions", isDirectory: true),
            claudeHome.appendingPathComponent("projects", isDirectory: true),
        ]
    }

    static func productionWatchRoots() -> [URL] {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return watchRoots(
            claudeHome: home.appendingPathComponent(".claude", isDirectory: true),
            desktopSupport: home.appendingPathComponent("Library/Application Support/Claude", isDirectory: true)
        )
    }

    /// The environment both lanes run `scoutctl` with. Launched from the Dock,
    /// Finder or as a login item, Scout inherits only
    /// `/usr/bin:/bin:/usr/sbin:/sbin`, where the engine's `shutil.which("gh")`
    /// finds nothing. The user's tool directories go first, as scout-plugin's
    /// own `probe_env` does; the rest of the inherited PATH follows, deduplicated.
    static func engineEnvironment(
        home: URL = FileManager.default.homeDirectoryForCurrentUser,
        inherited: [String: String] = ProcessInfo.processInfo.environment
    ) -> [String: String] {
        let toolDirectories = [home.appendingPathComponent(".local/bin").path, "/opt/homebrew/bin", "/usr/local/bin"]
        let inheritedPath = inherited["PATH"].flatMap { $0.isEmpty ? nil : $0 } ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        var seen = Set<String>()
        let path = (toolDirectories + inheritedPath.split(separator: ":").map(String.init))
            .filter { !$0.isEmpty && seen.insert($0).inserted }
        return ["PATH": path.joined(separator: ":")]
    }

    /// True for a write the engine itself makes under the vault's
    /// `.scout-cache/` (1b spec §3.6): `sessions-index.json`, every
    /// `sessions-*.cache.json`, and the `.<name>.<random>.tmp` files its atomic
    /// writes go through. A refresh must never be triggered by one of these, or
    /// every build would schedule the next.
    static func isEngineOwnWrite(_ url: URL, cacheDirectory: URL) -> Bool {
        let parent = url.deletingLastPathComponent().resolvingSymlinksInPath().standardizedFileURL.path
        let cache = cacheDirectory.resolvingSymlinksInPath().standardizedFileURL.path
        guard parent == cache else { return false }
        var name = Substring(url.lastPathComponent)
        if name.hasPrefix("."), name.hasSuffix(".tmp") {
            // `.sessions-index.json.k3j9x2qa.tmp` → `sessions-index.json`
            name = name.dropFirst().dropLast(4)
            guard let dot = name.lastIndex(of: ".") else { return false }
            name = name[..<dot]
        }
        return name == "sessions-index.json"
            || (name.hasPrefix("sessions-") && name.hasSuffix(".cache.json"))
    }
}
