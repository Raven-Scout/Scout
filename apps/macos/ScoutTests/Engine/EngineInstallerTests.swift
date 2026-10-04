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
    }

    /// Lock-guarded progress recorder — a `@Sendable` sink mutating a bare
    /// `var` directly from the actor's call site is a data race waiting to
    /// happen even when today's tests happen to run serially (Ruling 54
    /// minor 8).
    final class ProgressRecorder: @unchecked Sendable {
        private let lock = NSLock()
        private var items: [InstallProgress] = []
        func append(_ item: InstallProgress) { lock.withLock { items.append(item) } }
        var all: [InstallProgress] { lock.withLock { items } }
    }

    /// A tiny plugin tree tarred like `git archive` (no top-level prefix), plus
    /// a fake install-venv.sh that honors SCOUT_VENV_DIR by creating scoutctl.
    func fixture(version: String = "0.10.0") throws -> Fixture {
        let home = fm.temporaryDirectory.appendingPathComponent("installer-\(UUID().uuidString)")
        let layout = EngineLayout(home: home)
        let tarball = try buildTarball(version: version, in: home)
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

    /// Build a `scout-engine-<version>.tar.gz` under `dir` for a second
    /// version, reusable against an existing fixture's layout/runner to
    /// exercise an in-place upgrade.
    @discardableResult
    func buildTarball(version: String, in dir: URL) throws -> URL {
        let tree = dir.appendingPathComponent("tree-\(version)-\(UUID().uuidString)")
        try fm.createDirectory(at: tree.appendingPathComponent(".claude-plugin"), withIntermediateDirectories: true)
        try fm.createDirectory(at: tree.appendingPathComponent("scripts"), withIntermediateDirectories: true)
        try #"{"name": "scout", "version": "\#(version)"}"#.write(to: tree.appendingPathComponent(".claude-plugin/plugin.json"), atomically: true, encoding: .utf8)
        try "#!/bin/bash\nmkdir -p \"$SCOUT_VENV_DIR/bin\"; printf '#!/bin/sh\\necho \(version)\\n' > \"$SCOUT_VENV_DIR/bin/scoutctl\"; chmod +x \"$SCOUT_VENV_DIR/bin/scoutctl\"\n"
            .write(to: tree.appendingPathComponent("scripts/install-venv.sh"), atomically: true, encoding: .utf8)
        let tarball = dir.appendingPathComponent("scout-engine-\(version).tar.gz")
        let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        p.arguments = ["-czf", tarball.path, "-C", tree.path, "."]
        try p.run(); p.waitUntilExit()
        return tarball
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
        // `current` must resolve to the real, absolute versioned root (Ruling
        // 54 critical 1) — not just a path ending in the version number, and
        // reading through the link must actually work.
        #expect(try fm.destinationOfSymbolicLink(atPath: f.layout.currentEngineLink.path) == f.layout.engineRoot(version: "0.10.0").path)
        #expect(fm.fileExists(atPath: f.layout.currentEngineLink.appendingPathComponent(".claude-plugin/plugin.json").path))
        #expect(fm.isExecutableFile(atPath: f.layout.scoutctl(version: "0.10.0").path))
        #expect(!fm.fileExists(atPath: f.layout.engineRoot(version: "0.10.0").path + ".partial"))
        #expect(f.runner.calls(to: "claude") == [ClaudeCodeCLI.marketplaceAdd(path: f.layout.currentEngineLink), ClaudeCodeCLI.pluginInstall])
        let venvCall = f.runner.calls.first { $0.arguments.first?.hasSuffix("install-venv.sh") == true }
        #expect(venvCall?.environment["SCOUT_VENV_DIR"] == f.layout.venv(version: "0.10.0").path)
        #expect(venvCall?.environment["SCOUT_VENV_EXTRAS"] == "full")
        #expect(venvCall?.environment["SCOUT_UV"] == f.layout.uvURL.path)
    }

    /// Ruling 54 critical 2: repointing `current` a second time used to
    /// always fail (`FileManager.replaceItemAt` refuses a symlink as the
    /// original). Re-running `.unpackEngine` for an already-unpacked version
    /// must be a clean, successful skip.
    @Test func unpackEngineIsIdempotentOnASecondRun() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        let first = await installer(f) { _ in }.run(steps: [.unpackEngine], mode: .upgrade(vault: f.layout.home))
        #expect(first)
        let seen = ProgressRecorder()
        let second = await installer(f) { seen.append($0) }.run(steps: [.unpackEngine], mode: .upgrade(vault: f.layout.home))
        #expect(second)
        guard case .skipped(let reason)? = seen.all.last?.status else { Issue.record("expected skipped"); return }
        #expect(reason.contains("already unpacked"))
        #expect(try fm.destinationOfSymbolicLink(atPath: f.layout.currentEngineLink.path) == f.layout.engineRoot(version: "0.10.0").path)
    }

    /// Ruling 54 critical 1/2: unpacking a NEW version repoints `current` to
    /// it (an absolute target) without disturbing the previous version's
    /// files, and leaves no `current.tmp` behind.
    @Test func unpackUpgradesToANewVersionLeavingThePreviousVersionIntact() async throws {
        let f10 = try fixture(version: "0.10.0")
        defer { try? fm.removeItem(at: f10.layout.home) }
        let ok10 = await installer(f10) { _ in }.run(steps: [.unpackEngine], mode: .upgrade(vault: f10.layout.home))
        #expect(ok10)

        let tarball11 = try buildTarball(version: "0.11.0", in: f10.layout.home)
        let release11 = EngineRelease(schemaVersion: 1, engine: .init(repo: "example-org/scout-plugin", version: "0.11.0", tag: "v0.11.0", commit: String(repeating: "c", count: 40)), uv: f10.release.uv)
        let f11 = Fixture(layout: f10.layout, release: release11, tarball: tarball11, runner: f10.runner)
        let ok11 = await installer(f11) { _ in }.run(steps: [.unpackEngine], mode: .upgrade(vault: f10.layout.home))
        #expect(ok11)

        #expect(try fm.destinationOfSymbolicLink(atPath: f10.layout.currentEngineLink.path) == f10.layout.engineRoot(version: "0.11.0").path)
        #expect(fm.fileExists(atPath: f10.layout.engineRoot(version: "0.10.0").appendingPathComponent(".claude-plugin/plugin.json").path))
        #expect(fm.fileExists(atPath: f10.layout.engineRoot(version: "0.11.0").appendingPathComponent(".claude-plugin/plugin.json").path))
        #expect(!fm.fileExists(atPath: f10.layout.engineDir.appendingPathComponent("current.tmp").path))
    }

    /// Only tests the hard-failure path for a marketplace from another
    /// source entirely (github) — renamed from
    /// `registerUpdatesWhenAlreadyInstalledAndStopsOnForeignMarketplace`,
    /// which claimed to test the "already installed → update" branch but
    /// never seeded `installed_plugins.json` to exercise it (Ruling 54
    /// important 3). See `registerRunsPluginUpdateWhenAlreadyInstalledAndMarketplaceIsOurs`
    /// for that case.
    @Test func registerStopsOnAForeignGithubMarketplace() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        try fm.createDirectory(at: f.layout.claudePluginsDir, withIntermediateDirectories: true)
        try #"{"scout-plugin": {"source": {"source": "github", "repo": "example-org/scout-plugin"}}}"#.write(to: f.layout.claudePluginsDir.appendingPathComponent("known_marketplaces.json"), atomically: true, encoding: .utf8)
        let seen = ProgressRecorder()
        let ok = await installer(f) { seen.append($0) }.run(steps: [.registerWithClaudeCode], mode: .upgrade(vault: f.layout.home))
        #expect(!ok)
        guard case .failed(let why)? = seen.all.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("scout-plugin") && why.contains("github"))
        #expect(f.runner.calls(to: "claude").isEmpty)
    }

    /// The actual "already installed, marketplace is ours → update not
    /// install" path (Ruling 54 important 3).
    @Test func registerRunsPluginUpdateWhenAlreadyInstalledAndMarketplaceIsOurs() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine], mode: .upgrade(vault: f.layout.home))
        try fm.createDirectory(at: f.layout.claudePluginsDir, withIntermediateDirectories: true)
        try #"{"scout-plugin": {"source": {"source": "directory", "path": "\#(f.layout.currentEngineLink.path)"}}}"#
            .write(to: f.layout.claudePluginsDir.appendingPathComponent("known_marketplaces.json"), atomically: true, encoding: .utf8)
        try #"{"plugins": {"scout@scout-plugin": [{"version": "0.10.0", "installPath": "\#(f.layout.currentEngineLink.path)"}]}}"#
            .write(to: f.layout.claudePluginsDir.appendingPathComponent("installed_plugins.json"), atomically: true, encoding: .utf8)
        f.runner.on(tool: "claude", prefix: ["plugin", "update"])
        let ok = await installer(f) { _ in }.run(steps: [.registerWithClaudeCode], mode: .upgrade(vault: f.layout.home))
        #expect(ok)
        #expect(f.runner.calls(to: "claude") == [ClaudeCodeCLI.pluginUpdate])
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
        let seen = ProgressRecorder()
        let ok = await installer(f) { seen.append($0) }.run(steps: [.registerWithClaudeCode], mode: .upgrade(vault: f.layout.home))
        #expect(!ok)
        guard case .failed(let why)? = seen.all.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("scout-plugin") && why.contains("directory") && why.contains(foreign.path))
        #expect(f.runner.calls(to: "claude").isEmpty)
    }

    /// Ruling 54 minor 4: a `.partial` directory under `engineDir` — even one
    /// whose manifest fully matches a real version, as it would mid-install —
    /// must NEVER be accepted as "ours"; only a canonical `<engineDir>/<v>`
    /// root is.
    @Test func registerRejectsAPartialEngineDirectoryEvenWithAMatchingManifest() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        try fm.createDirectory(at: f.layout.claudePluginsDir, withIntermediateDirectories: true)
        let partial = URL(fileURLWithPath: f.layout.engineRoot(version: "0.10.0").path + ".partial")
        try fm.createDirectory(at: partial.appendingPathComponent(".claude-plugin"), withIntermediateDirectories: true)
        try #"{"name": "scout", "version": "0.10.0"}"#.write(to: partial.appendingPathComponent(".claude-plugin/plugin.json"), atomically: true, encoding: .utf8)
        try #"{"scout-plugin": {"source": {"source": "directory", "path": "\#(partial.path)"}}}"#
            .write(to: f.layout.claudePluginsDir.appendingPathComponent("known_marketplaces.json"), atomically: true, encoding: .utf8)
        let seen = ProgressRecorder()
        let ok = await installer(f) { seen.append($0) }.run(steps: [.registerWithClaudeCode], mode: .upgrade(vault: f.layout.home))
        #expect(!ok)
        guard case .failed(let why)? = seen.all.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("scout-plugin") && why.contains(partial.path))
        #expect(f.runner.calls(to: "claude").isEmpty)
    }

    /// Ruling 54 important 3: the pre-check (`scoutctl version` already
    /// reports the pinned version) must skip without ever invoking
    /// install-venv.sh.
    @Test func buildVenvSkipsWhenScoutctlAlreadyReportsTheVersion() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine], mode: .upgrade(vault: f.layout.home))
        try fm.createDirectory(at: f.layout.venv(version: "0.10.0").appendingPathComponent("bin"), withIntermediateDirectories: true)
        try "#!/bin/sh\n".write(to: f.layout.scoutctl(version: "0.10.0"), atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: f.layout.scoutctl(version: "0.10.0").path)
        let seen = ProgressRecorder()
        let ok = await installer(f) { seen.append($0) }.run(steps: [.buildVenv], mode: .upgrade(vault: f.layout.home))
        #expect(ok)
        guard case .skipped(let reason)? = seen.all.last?.status else { Issue.record("expected skipped"); return }
        #expect(reason.contains("already built"))
        #expect(!f.runner.calls.contains { $0.arguments.first?.hasSuffix("install-venv.sh") == true })
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

    /// A yellow doctor embedded in a successful `bootstrap auto` payload
    /// still counts as success — only a `red` doctor (or `action: refused`)
    /// fails the step. The engine may also exit non-zero in this case
    /// (Click/typer quirks); only the decoded payload matters.
    @Test func bootstrapVaultSucceedsWithYellowDoctorEvenOnNonZeroExit() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        let vault = f.layout.home.appendingPathComponent("Scout")
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine, .buildVenv], mode: .upgrade(vault: vault))
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: #"{"schema_version":1,"action":"install","reason":"","dry_run":false,"vault":"\#(vault.path)","plugin_version":"0.10.0","error":null,"doctor":{"severity":"yellow","errors":[],"warnings":["snapshot missing: x"]},"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":null}"#, exit: 1)
        let input = BootstrapInput(vault: vault, userName: "Alex", userEmail: "alex@example.com", timezone: "Europe/Prague")
        let ok = await installer(f) { _ in }.run(steps: [.bootstrapVault], mode: .install(input))
        #expect(ok)
    }

    @Test func bootstrapVaultFailsWhenEngineRefuses() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        let vault = f.layout.home.appendingPathComponent("Scout")
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine, .buildVenv], mode: .upgrade(vault: vault))
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: #"{"schema_version":1,"action":"refused","reason":"","dry_run":false,"vault":"\#(vault.path)","plugin_version":"0.10.0","error":"install needs --user-name","doctor":null,"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":null}"#)
        let seen = ProgressRecorder()
        let ok = await installer(f) { seen.append($0) }.run(steps: [.bootstrapVault], mode: .upgrade(vault: vault))
        #expect(!ok)
        guard case .failed(let why)? = seen.all.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("install needs --user-name"))
    }

    @Test func bootstrapVaultFailsWithAStderrPreviewWhenStdoutIsNotJSON() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        let vault = f.layout.home.appendingPathComponent("Scout")
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine, .buildVenv], mode: .upgrade(vault: vault))
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: "not json", stderr: "Traceback: boom", exit: 1)
        let seen = ProgressRecorder()
        let ok = await installer(f) { seen.append($0) }.run(steps: [.bootstrapVault], mode: .upgrade(vault: vault))
        #expect(!ok)
        guard case .failed(let why)? = seen.all.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("Traceback: boom"))
    }

    @Test func redDoctorFailsTheBootstrapStep() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        let vault = f.layout.home.appendingPathComponent("Scout")
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine, .buildVenv], mode: .upgrade(vault: vault))
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "auto"], stdout: #"{"schema_version":1,"action":"upgrade","reason":"","dry_run":false,"vault":"/v","plugin_version":"0.10.0","error":null,"doctor":{"severity":"red","errors":["launchd: com.scout.heartbeat not registered"],"warnings":[]},"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":null}"#, exit: 2)
        let seen = ProgressRecorder()
        let ok = await installer(f) { seen.append($0) }.run(steps: [.bootstrapVault], mode: .upgrade(vault: vault))
        #expect(!ok)
        guard case .failed(let why)? = seen.all.last?.status else { Issue.record("expected failure"); return }
        #expect(why.contains("heartbeat"))
    }

    @Test func verifyPassesOnGreenDoctor() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        let vault = f.layout.home.appendingPathComponent("Scout")
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine, .buildVenv], mode: .upgrade(vault: vault))
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor", "--json"], stdout: #"{"severity": "green", "errors": [], "warnings": []}"#)
        let seen = ProgressRecorder()
        let ok = await installer(f) { seen.append($0) }.run(steps: [.verify], mode: .upgrade(vault: vault))
        #expect(ok)
        guard case .done? = seen.all.last?.status else { Issue.record("expected done"); return }
    }

    @Test func verifyFailsOnRedDoctorNamingTheError() async throws {
        let f = try fixture()
        defer { try? fm.removeItem(at: f.layout.home) }
        let vault = f.layout.home.appendingPathComponent("Scout")
        _ = await installer(f) { _ in }.run(steps: [.unpackEngine, .buildVenv], mode: .upgrade(vault: vault))
        f.runner.on(tool: "scoutctl", prefix: ["bootstrap", "doctor", "--json"], stdout: #"{"severity": "red", "errors": ["launchd: com.scout.heartbeat not registered"], "warnings": []}"#)
        let seen = ProgressRecorder()
        let ok = await installer(f) { seen.append($0) }.run(steps: [.verify], mode: .upgrade(vault: vault))
        #expect(!ok)
        guard case .failed(let why)? = seen.all.last?.status else { Issue.record("expected failure"); return }
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
