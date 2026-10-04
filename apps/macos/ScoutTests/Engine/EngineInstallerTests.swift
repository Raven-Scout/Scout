import Testing
import Foundation
@testable import Scout

@Suite("EngineInstaller")
struct EngineInstallerTests {
    let fm = FileManager.default

    struct Fixture {
        let layout: EngineLayout
        let release: EngineRelease
        let tarball: URL
        let runner: RuleBasedRunner
        let claude = URL(fileURLWithPath: "/Users/alex/.local/bin/claude")
        var progress: [InstallProgress] = []
    }

    /// A tiny plugin tree tarred like `git archive` (no top-level prefix), plus
    /// a fake install-venv.sh that honors SCOUT_VENV_DIR by creating scoutctl.
    func fixture(version: String = "0.10.0") throws -> Fixture {
        let home = fm.temporaryDirectory.appendingPathComponent("installer-\(UUID().uuidString)")
        let layout = EngineLayout(home: home)
        let tree = home.appendingPathComponent("tree")
        try fm.createDirectory(at: tree.appendingPathComponent(".claude-plugin"), withIntermediateDirectories: true)
        try fm.createDirectory(at: tree.appendingPathComponent("scripts"), withIntermediateDirectories: true)
        try #"{"name": "scout", "version": "\#(version)"}"#.write(to: tree.appendingPathComponent(".claude-plugin/plugin.json"), atomically: true, encoding: .utf8)
        try "#!/bin/bash\nmkdir -p \"$SCOUT_VENV_DIR/bin\"; printf '#!/bin/sh\\necho \(version)\\n' > \"$SCOUT_VENV_DIR/bin/scoutctl\"; chmod +x \"$SCOUT_VENV_DIR/bin/scoutctl\"\n"
            .write(to: tree.appendingPathComponent("scripts/install-venv.sh"), atomically: true, encoding: .utf8)
        let tarball = home.appendingPathComponent("scout-engine-\(version).tar.gz")
        let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        p.arguments = ["-czf", tarball.path, "-C", tree.path, "."]
        try p.run(); p.waitUntilExit()
        let release = EngineRelease(schemaVersion: 1, engine: .init(repo: "example-org/scout-plugin", version: version, tag: "v\(version)", commit: String(repeating: "a", count: 40)),
                                    uv: .init(version: "0.12.1", sha256: [:]))
        // uv already present so ensureUv short-circuits without a network.
        try fm.createDirectory(at: layout.localBin, withIntermediateDirectories: true)
        try "#!/bin/sh\n".write(to: layout.uvURL, atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: layout.uvURL.path)
        let runner = RuleBasedRunner()
        // Real tar for extraction; everything else is scripted.
        runner.on({ url, _ in url.path == "/usr/bin/tar" }) { url, args, _ in
            try await SystemProcessRunner().run(executable: url, arguments: args, environment: [:], workingDirectory: nil)
        }
        // `bash install-venv.sh` really runs the fake script so the venv appears.
        runner.on({ url, args in url.path == "/bin/bash" && args.first?.hasSuffix("install-venv.sh") == true }) { url, args, env in
            try await SystemProcessRunner().run(executable: url, arguments: args, environment: env, workingDirectory: nil)
        }
        runner.on({ url, args in url.lastPathComponent == "scoutctl" && args == ["version"] }, { _, _, _ in ProcessResult(exitCode: 0, stdout: Data("\(version)\n".utf8), stderr: Data()) })
        return Fixture(layout: layout, release: release, tarball: tarball, runner: runner)
    }

    func installer(_ f: Fixture, sink: @escaping @Sendable (InstallProgress) -> Void) -> EngineInstaller {
        EngineInstaller(layout: f.layout, release: f.release, tarballURL: f.tarball, runner: f.runner,
                        uv: UvInstaller(release: f.release.uv, layout: f.layout, downloader: URLSessionDownloader(), runner: f.runner),
                        claude: f.claude, progress: sink)
    }

    @Test func unpackBuildRegisterProducesTheCanonicalLayout() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        f.runner.on(tool: "claude", prefix: ["plugin", "marketplace", "add"])
        f.runner.on(tool: "claude", prefix: ["plugin", "install"])
        let ok = await installer(f) { _ in }.run(steps: [.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode], mode: .upgrade(vault: f.layout.home.appendingPathComponent("Scout")))
        #expect(ok)
        #expect(fm.fileExists(atPath: f.layout.engineRoot(version: "0.10.0").appendingPathComponent(".claude-plugin/plugin.json").path))
        #expect(try fm.destinationOfSymbolicLink(atPath: f.layout.currentEngineLink.path).hasSuffix("0.10.0"))
        #expect(fm.isExecutableFile(atPath: f.layout.scoutctl(version: "0.10.0").path))
        #expect(!fm.fileExists(atPath: f.layout.engineRoot(version: "0.10.0").path + ".partial"))
        #expect(f.runner.calls(to: "claude") == [ClaudeCodeCLI.marketplaceAdd(path: f.layout.currentEngineLink), ClaudeCodeCLI.pluginInstall])
        let venvCall = f.runner.calls.first { $0.arguments.first?.hasSuffix("install-venv.sh") == true }
        #expect(venvCall?.environment["SCOUT_VENV_DIR"] == f.layout.venv(version: "0.10.0").path)
        #expect(venvCall?.environment["SCOUT_VENV_EXTRAS"] == "full")
        #expect(venvCall?.environment["SCOUT_UV"] == f.layout.uvURL.path)
    }

    @Test func registerUpdatesWhenAlreadyInstalledAndStopsOnForeignMarketplace() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        try fm.createDirectory(at: f.layout.claudePluginsDir, withIntermediateDirectories: true)
        try #"{"scout-plugin": {"source": {"source": "github", "repo": "example-org/scout-plugin"}}}"#.write(to: f.layout.claudePluginsDir.appendingPathComponent("known_marketplaces.json"), atomically: true, encoding: .utf8)
        var seen: [InstallProgress] = []
        let ok = await installer(f) { seen.append($0) }.run(steps: [.registerWithClaudeCode], mode: .upgrade(vault: f.layout.home))
        #expect(!ok)
        guard case .failed(let why)? = seen.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("scout-plugin") && why.contains("github"))
        #expect(f.runner.calls(to: "claude").isEmpty)
    }

    /// Ruling 53: Claude Code may have recorded the `current` symlink's own
    /// path as the marketplace directory — that's still ours.
    @Test func registerAcceptsMarketplaceAtCurrentEngineLinkPath() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine], mode: .upgrade(vault: f.layout.home))
        try fm.createDirectory(at: f.layout.claudePluginsDir, withIntermediateDirectories: true)
        try #"{"scout-plugin": {"source": {"source": "directory", "path": "\#(f.layout.currentEngineLink.path)"}}}"#
            .write(to: f.layout.claudePluginsDir.appendingPathComponent("known_marketplaces.json"), atomically: true, encoding: .utf8)
        f.runner.on(tool: "claude", prefix: ["plugin", "install"])
        let ok = await installer(f) { _ in }.run(steps: [.registerWithClaudeCode], mode: .upgrade(vault: f.layout.home))
        #expect(ok)
        #expect(f.runner.calls(to: "claude") == [ClaudeCodeCLI.pluginInstall])
    }

    /// Ruling 53: Claude Code may instead have recorded the symlink's
    /// *resolved* target (the versioned engine root) — also ours.
    @Test func registerAcceptsMarketplaceAtResolvedEngineRootPath() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine], mode: .upgrade(vault: f.layout.home))
        try fm.createDirectory(at: f.layout.claudePluginsDir, withIntermediateDirectories: true)
        try #"{"scout-plugin": {"source": {"source": "directory", "path": "\#(f.layout.engineRoot(version: "0.10.0").path)"}}}"#
            .write(to: f.layout.claudePluginsDir.appendingPathComponent("known_marketplaces.json"), atomically: true, encoding: .utf8)
        f.runner.on(tool: "claude", prefix: ["plugin", "install"])
        let ok = await installer(f) { _ in }.run(steps: [.registerWithClaudeCode], mode: .upgrade(vault: f.layout.home))
        #expect(ok)
        #expect(f.runner.calls(to: "claude") == [ClaudeCodeCLI.pluginInstall])
    }

    /// Ruling 53: a directory marketplace that is NOT under the managed
    /// engine dir stays a hard failure naming the foreign source (spec §10 —
    /// external engines are never silently adopted/replaced).
    @Test func registerRejectsDirectoryMarketplaceOutsideTheManagedEngineDir() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        try fm.createDirectory(at: f.layout.claudePluginsDir, withIntermediateDirectories: true)
        let foreign = f.layout.home.appendingPathComponent("elsewhere/scout-plugin")
        try fm.createDirectory(at: foreign, withIntermediateDirectories: true)
        try #"{"scout-plugin": {"source": {"source": "directory", "path": "\#(foreign.path)"}}}"#
            .write(to: f.layout.claudePluginsDir.appendingPathComponent("known_marketplaces.json"), atomically: true, encoding: .utf8)
        var seen: [InstallProgress] = []
        let ok = await installer(f) { seen.append($0) }.run(steps: [.registerWithClaudeCode], mode: .upgrade(vault: f.layout.home))
        #expect(!ok)
        guard case .failed(let why)? = seen.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("scout-plugin") && why.contains("directory") && why.contains(foreign.path))
        #expect(f.runner.calls(to: "claude").isEmpty)
    }

    @Test func bootstrapVaultPassesTheContractArgvAndDecodesTheResult() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        let vault = f.layout.home.appendingPathComponent("Scout")
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: #"{"schema_version":1,"action":"install","reason":"","dry_run":false,"vault":"\#(vault.path)","plugin_version":"0.10.0","error":null,"doctor":{"severity":"green","errors":[],"warnings":[]},"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":"p"}"#)
        // Pretend unpack+venv already happened.
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine, .buildVenv], mode: .upgrade(vault: vault))
        let input = BootstrapInput(vault: vault, userName: "Alex", userEmail: "alex@example.com", timezone: "Europe/Prague", connectors: ["github", "slack"], userSlackID: "U0123", githubUsername: "alex", githubRepos: "example-org/a,example-org/b", maxBudget: "8.00")
        let ok = await installer(f) { _ in }.run(steps: [.bootstrapVault], mode: .install(input))
        #expect(ok)
        let call = f.runner.calls(to: "scoutctl").last!
        #expect(call == EngineInstaller.bootstrapAutoArguments(mode: .install(input), claude: f.claude))
        #expect(call.contains("--no-interactive") && call.contains("--yes") && call.contains("--json") && call.contains("--managed-by") && call.contains("scout-app"))
        #expect(call[call.firstIndex(of: "--connectors")! + 1] == "github,slack")
        #expect(f.runner.calls.last?.environment["SCOUT_DATA_DIR"] == vault.path)
    }

    @Test func redDoctorFailsTheBootstrapStep() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        let vault = f.layout.home.appendingPathComponent("Scout")
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine, .buildVenv], mode: .upgrade(vault: vault))
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: #"{"schema_version":1,"action":"upgrade","reason":"","dry_run":false,"vault":"/v","plugin_version":"0.10.0","error":null,"doctor":{"severity":"red","errors":["launchd: com.scout.heartbeat not registered"],"warnings":[]},"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":null}"#, exit: 2)
        var seen: [InstallProgress] = []
        let ok = await installer(f) { seen.append($0) }.run(steps: [.bootstrapVault], mode: .upgrade(vault: vault))
        #expect(!ok)
        guard case .failed(let why)? = seen.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("heartbeat"))
    }

    @Test func manifestMismatchLeavesNoEngineBehind() async throws {
        var f = try fixture(version: "0.10.0")
        defer { try? fm.removeItem(at: f.layout.home) }
        f = Fixture(layout: f.layout, release: EngineRelease(schemaVersion: 1, engine: .init(repo: "x", version: "0.11.0", tag: "v0.11.0", commit: String(repeating: "b", count: 40)), uv: f.release.uv), tarball: f.tarball, runner: f.runner)
        let ok = await installer(f) { _ in }.run(steps: [.unpackEngine], mode: .upgrade(vault: f.layout.home))
        #expect(!ok)
        #expect(!fm.fileExists(atPath: f.layout.engineRoot(version: "0.11.0").path))
        #expect(!fm.fileExists(atPath: f.layout.currentEngineLink.path))
    }
}
