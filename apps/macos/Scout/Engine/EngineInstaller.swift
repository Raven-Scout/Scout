import Foundation

nonisolated enum InstallStep: String, CaseIterable, Sendable {
    case ensureUv, unpackEngine, buildVenv, registerWithClaudeCode, bootstrapVault, verify

    var title: String {
        switch self {
        case .ensureUv: return "Get uv (Python)"
        case .unpackEngine: return "Unpack engine"
        case .buildVenv: return "Build Python environment"
        case .registerWithClaudeCode: return "Register with Claude Code"
        case .bootstrapVault: return "Set up vault and schedule"
        case .verify: return "Verify"
        }
    }
}

nonisolated enum StepStatus: Equatable, Sendable { case pending, running, done, skipped(String), failed(String) }

nonisolated struct InstallProgress: Equatable, Sendable {
    let step: InstallStep
    let status: StepStatus
    let log: String
}

/// Everything `scoutctl bootstrap auto` needs for a fresh install (spec §5 steps 4–5).
nonisolated struct BootstrapInput: Equatable, Sendable {
    var vault: URL
    var instanceName: String = "Scout"
    var userName: String
    var userEmail: String
    var timezone: String
    var connectors: Set<String> = []
    var userSlackID: String = ""
    var githubUsername: String = ""
    var githubRepos: String = ""
    var maxBudget: String = "5.00"
}

nonisolated enum InstallMode: Equatable, Sendable {
    case install(BootstrapInput)
    case upgrade(vault: URL)

    var vault: URL {
        switch self {
        case .install(let i): return i.vault
        case .upgrade(let v): return v
        }
    }
}

/// The six idempotent steps of spec §4.4, each independently re-runnable.
/// Nothing half-done ever looks whole: partial unpacks carry `.partial`,
/// `current` is repointed only after the manifest check, and the pointer is
/// written by the engine itself once its venv has run.
actor EngineInstaller {
    private let layout: EngineLayout
    private let release: EngineRelease
    private let tarballURL: URL?
    private let runner: any ProcessRunner
    private let uv: UvInstaller
    private let claude: URL
    private let progress: @Sendable (InstallProgress) -> Void
    private var uvPath: URL?

    init(layout: EngineLayout, release: EngineRelease, tarballURL: URL?, runner: any ProcessRunner, uv: UvInstaller,
         claude: URL, progress: @escaping @Sendable (InstallProgress) -> Void) {
        self.layout = layout; self.release = release; self.tarballURL = tarballURL; self.runner = runner
        self.uv = uv; self.claude = claude; self.progress = progress
    }

    private var version: String { release.engine.version }
    private var engineRoot: URL { layout.engineRoot(version: version) }
    private var scoutctl: URL { layout.scoutctl(version: version) }

    func run(steps: [InstallStep], mode: InstallMode) async -> Bool {
        for step in steps {
            report(step, .running, "")
            do {
                let note = try await perform(step, mode: mode)
                report(step, .done, note)
            } catch let skip as Skipped {
                report(step, .skipped(skip.reason), skip.reason)
            } catch {
                report(step, .failed(String(describing: error)), String(describing: error))
                return false
            }
        }
        return true
    }

    // MARK: steps

    private nonisolated struct Skipped: Error { let reason: String }
    private nonisolated struct Failure: Error, CustomStringConvertible { let description: String }

    private func perform(_ step: InstallStep, mode: InstallMode) async throws -> String {
        switch step {
        case .ensureUv:
            let path = try await uv.ensure { [progress] line in progress(InstallProgress(step: .ensureUv, status: .running, log: line)) }
            uvPath = path
            return path.path
        case .unpackEngine:
            return try await unpackEngine()
        case .buildVenv:
            return try await buildVenv()
        case .registerWithClaudeCode:
            return try await registerWithClaudeCode()
        case .bootstrapVault:
            return try await bootstrapVault(mode: mode)
        case .verify:
            let result = try await runner.run(executable: scoutctl, arguments: ["bootstrap", "doctor", "--json"],
                                              environment: ["SCOUT_DATA_DIR": mode.vault.path], workingDirectory: nil)
            guard let report = DoctorReport.parse(stdout: result.stdout, stderr: result.stderr) else { throw Failure(description: "doctor output not understood") }
            if report.severity == .red { throw Failure(description: report.errors.joined(separator: "; ")) }
            return "doctor: \(report.severity.rawValue)" + (report.warnings.isEmpty ? "" : " — " + report.warnings.joined(separator: "; "))
        }
    }

    private func unpackEngine() async throws -> String {
        if EngineLocator.version(atRoot: engineRoot) == version {
            try repointCurrent()
            throw Skipped(reason: "engine \(version) already unpacked")
        }
        guard let tarballURL else { throw Failure(description: "this build carries no engine tarball (Debug build without a source)") }
        let fileManager = FileManager.default
        let partial = URL(fileURLWithPath: engineRoot.path + ".partial")
        try? fileManager.removeItem(at: partial)
        try fileManager.createDirectory(at: partial, withIntermediateDirectories: true)
        let tar = try await runner.run(executable: URL(fileURLWithPath: "/usr/bin/tar"), arguments: ["-xzf", tarballURL.path, "-C", partial.path],
                                       environment: [:], workingDirectory: nil)
        guard tar.exitCode == 0 else { throw Failure(description: "tar failed: \(String(data: tar.stderr, encoding: .utf8) ?? "")") }
        guard EngineLocator.version(atRoot: partial) == version else {
            try? fileManager.removeItem(at: partial)
            throw Failure(description: "bundled engine manifest does not match pinned version \(version)")
        }
        try? fileManager.removeItem(at: engineRoot)
        try fileManager.moveItem(at: partial, to: engineRoot)
        try repointCurrent()
        return engineRoot.path
    }

    /// Atomic symlink swap: create `current.tmp`, then rename over `current`.
    private func repointCurrent() throws {
        let fileManager = FileManager.default
        try fileManager.createDirectory(at: layout.engineDir, withIntermediateDirectories: true)
        let tmp = layout.engineDir.appendingPathComponent("current.tmp")
        try? fileManager.removeItem(at: tmp)
        try fileManager.createSymbolicLink(at: tmp, withDestinationURL: URL(fileURLWithPath: version, relativeTo: layout.engineDir))
        if (try? fileManager.destinationOfSymbolicLink(atPath: layout.currentEngineLink.path)) != nil || fileManager.fileExists(atPath: layout.currentEngineLink.path) {
            _ = try fileManager.replaceItemAt(layout.currentEngineLink, withItemAt: tmp)
        } else {
            try fileManager.moveItem(at: tmp, to: layout.currentEngineLink)
        }
    }

    static func venvEnvironment(layout: EngineLayout, version: String, uv: URL) -> [String: String] {
        ["SCOUT_VENV_DIR": layout.venv(version: version).path, "SCOUT_UV": uv.path, "SCOUT_VENV_EXTRAS": "full",
         "HOME": layout.home.path, "PATH": "\(layout.localBin.path):/usr/bin:/bin"]
    }

    private func buildVenv() async throws -> String {
        let fileManager = FileManager.default
        let uvURL = uvPath ?? uv.existing() ?? layout.uvURL
        if fileManager.isExecutableFile(atPath: scoutctl.path),
           let v = try? await runner.run(executable: scoutctl, arguments: ["version"], environment: [:], workingDirectory: nil),
           String(data: v.stdout, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) == version {
            throw Skipped(reason: "venv for \(version) already built")
        }
        let script = engineRoot.appendingPathComponent("scripts/install-venv.sh")
        let build = try await runner.run(executable: URL(fileURLWithPath: "/bin/bash"), arguments: [script.path],
                                         environment: Self.venvEnvironment(layout: layout, version: version, uv: uvURL), workingDirectory: engineRoot)
        if build.exitCode != 0 {
            let preview = await ScheduleService.previewBytes(build.stderr.isEmpty ? build.stdout : build.stderr, max: 400)
            throw Failure(description: "install-venv.sh failed: \(preview)")
        }
        let check = try await runner.run(executable: scoutctl, arguments: ["version"], environment: [:], workingDirectory: nil)
        let got = String(data: check.stdout, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard got == version else { throw Failure(description: "scoutctl reports \(got ?? "nothing"), expected \(version)") }
        return scoutctl.path
    }

    /// An existing `scout-plugin` `.directory` marketplace counts as ours when
    /// its recorded path is `currentEngineLink` itself (standardized), when
    /// resolving symlinks on both sides lands on the same place (Claude Code
    /// may record the symlink's realpath instead of the link), or when it
    /// resolves to any directory directly under `engineDir` — a versioned
    /// engine root only this installer ever creates (Ruling 53). Anything
    /// else — a different directory, or a non-directory source — is a
    /// foreign marketplace we must never silently replace (spec §10).
    private func isManagedMarketplace(path: String) -> Bool {
        let raw = URL(fileURLWithPath: path).standardizedFileURL
        if raw == layout.currentEngineLink.standardizedFileURL { return true }
        let resolvedRaw = raw.resolvingSymlinksInPath().standardizedFileURL
        let resolvedCurrent = layout.currentEngineLink.resolvingSymlinksInPath().standardizedFileURL
        if resolvedRaw == resolvedCurrent { return true }
        return resolvedRaw.deletingLastPathComponent().standardizedFileURL == layout.engineDir.standardizedFileURL
    }

    private func registerWithClaudeCode() async throws -> String {
        let marketplace = ClaudePluginsRegistry.scoutMarketplace(pluginsDir: layout.claudePluginsDir)
        var notes: [String] = []
        switch marketplace?.source {
        case nil:
            let add = try await runner.run(executable: claude, arguments: ClaudeCodeCLI.marketplaceAdd(path: layout.currentEngineLink), environment: [:], workingDirectory: nil)
            if add.exitCode != 0 {
                let preview = await ScheduleService.previewBytes(add.stderr, max: 300)
                throw Failure(description: "claude plugin marketplace add failed: \(preview)")
            }
            notes.append("marketplace added")
        case .directory(let path) where isManagedMarketplace(path: path):
            notes.append("marketplace already points at the managed engine")
        case .some(let other):
            throw Failure(description: "Claude Code already has a 'scout-plugin' marketplace from another source (\(other)). Adopting it instead of replacing it — see Settings ▸ Engine → Migrate.")
        }
        let installed = ClaudePluginsRegistry.scoutPlugin(pluginsDir: layout.claudePluginsDir) != nil
        let args = installed ? ClaudeCodeCLI.pluginUpdate : ClaudeCodeCLI.pluginInstall
        let result = try await runner.run(executable: claude, arguments: args, environment: [:], workingDirectory: nil)
        if result.exitCode != 0 {
            let preview = await ScheduleService.previewBytes(result.stderr, max: 300)
            throw Failure(description: "claude \(args.joined(separator: " ")) failed: \(preview)")
        }
        notes.append(installed ? "plugin updated (restart Claude Code to load it)" : "plugin installed (restart Claude Code to load it)")
        return notes.joined(separator: "; ")
    }

    static func bootstrapAutoArguments(mode: InstallMode, claude: URL, managedBy: String = "scout-app") -> [String] {
        var args = ["bootstrap", "auto", "--no-interactive", "--yes", "--json", "--managed-by", managedBy, "--platform", "macos", "--claude-bin", claude.path]
        if case .install(let i) = mode {
            args += ["--instance-name", i.instanceName, "--user-name", i.userName, "--user-email", i.userEmail, "--timezone", i.timezone,
                     "--connectors", i.connectors.sorted().joined(separator: ","), "--user-slack-id", i.userSlackID,
                     "--github-username", i.githubUsername, "--github-repos", i.githubRepos, "--max-budget", i.maxBudget]
        }
        return args
    }

    private func bootstrapVault(mode: InstallMode) async throws -> String {
        let result = try await runner.run(executable: scoutctl, arguments: Self.bootstrapAutoArguments(mode: mode, claude: claude),
                                          environment: ["SCOUT_DATA_DIR": mode.vault.path], workingDirectory: nil)
        guard let decoded = BootstrapResult.parse(result.stdout) else {
            let preview = await ScheduleService.previewBytes(result.stderr.isEmpty ? result.stdout : result.stderr, max: 400)
            throw Failure(description: "bootstrap output not understood (exit \(result.exitCode)): \(preview)")
        }
        if decoded.action == "refused" { throw Failure(description: decoded.error ?? "bootstrap refused") }
        if let doctor = decoded.doctor, doctor.severity == .red { throw Failure(description: doctor.errors.joined(separator: "; ")) }
        var note = "\(decoded.action) → \(decoded.vault)"
        if let doctor = decoded.doctor { note += " (doctor: \(doctor.severity.rawValue))" }
        if !decoded.conflicts.isEmpty { note += "; conflicts to resolve: \(decoded.conflicts.joined(separator: ", "))" }
        return note
    }

    // MARK: reporting

    private func report(_ step: InstallStep, _ status: StepStatus, _ log: String) {
        progress(InstallProgress(step: step, status: status, log: log))
        appendLog("\(ISO8601DateFormatter().string(from: Date())) \(step.rawValue) \(status) \(log)\n")
    }

    private func appendLog(_ line: String) {
        let fileManager = FileManager.default
        try? fileManager.createDirectory(at: layout.stateDir, withIntermediateDirectories: true)
        if let handle = try? FileHandle(forWritingTo: layout.installLogURL) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: Data(line.utf8))
        } else {
            try? Data(line.utf8).write(to: layout.installLogURL)
        }
    }
}
