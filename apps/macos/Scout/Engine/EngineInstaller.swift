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

    /// Single-line preview of process output for error messages. A pure,
    /// `nonisolated` copy of `ScheduleService.previewBytes` (that one is
    /// `@MainActor`-isolated, so calling it from this actor just to format a
    /// string would mean an actor hop for every failure path — Ruling 54
    /// minor 6).
    private nonisolated static func preview(_ data: Data, max: Int) -> String {
        guard !data.isEmpty else { return "" }
        let raw = String(data: data.prefix(max), encoding: .utf8) ?? "<binary>"
        let oneLine = raw.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\r", with: " ").trimmingCharacters(in: .whitespaces)
        return data.count > max ? oneLine + "…" : oneLine
    }

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
        guard tar.exitCode == 0 else {
            try? fileManager.removeItem(at: partial)
            throw Failure(description: "tar failed: \(String(data: tar.stderr, encoding: .utf8) ?? "")")
        }
        guard EngineLocator.version(atRoot: partial) == version else {
            try? fileManager.removeItem(at: partial)
            throw Failure(description: "bundled engine manifest does not match pinned version \(version)")
        }
        try? fileManager.removeItem(at: engineRoot)
        try fileManager.moveItem(at: partial, to: engineRoot)
        try repointCurrent()
        return engineRoot.path
    }

    /// Atomic symlink swap: create `current.tmp` pointing at the ABSOLUTE
    /// engine root, then POSIX `rename(2)` it over `current`.
    ///
    /// Two bugs lived here (Ruling 54): (1) `URL(fileURLWithPath:relativeTo:)`
    /// treats a base with no trailing slash as a *file*, so the relative
    /// target `version` replaced `engine`'s last path component instead of
    /// being appended under it — `current` pointed at a nonexistent sibling
    /// of `engineDir`. Fixed by writing `engineRoot.path`, an absolute
    /// target, so there's nothing to resolve relative to. (2)
    /// `FileManager.replaceItemAt` refuses a symlink as the item being
    /// replaced ("doesn't exist", NSCocoaError 4 — it expects a regular
    /// file/directory, not a link), so repointing an *existing* `current`
    /// always failed. `rename(2)` replaces the link itself atomically
    /// without following it, on a fresh `current` or an existing one alike.
    private func repointCurrent() throws {
        let fileManager = FileManager.default
        try fileManager.createDirectory(at: layout.engineDir, withIntermediateDirectories: true)
        let tmp = layout.engineDir.appendingPathComponent("current.tmp")
        try? fileManager.removeItem(at: tmp)
        try fileManager.createSymbolicLink(atPath: tmp.path, withDestinationPath: engineRoot.path)
        guard rename(tmp.path, layout.currentEngineLink.path) == 0 else {
            let reason = String(cString: strerror(errno))
            try? fileManager.removeItem(at: tmp)
            throw Failure(description: "could not repoint the current engine symlink: \(reason)")
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
            let preview = Self.preview(build.stderr.isEmpty ? build.stdout : build.stderr, max: 400)
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
    /// resolves to the canonical versioned root for whatever version its own
    /// manifest claims (Ruling 53/54). Anything else — a different
    /// directory, a non-directory source, or a directory under `engineDir`
    /// that ISN'T a real versioned root (`<v>.partial`, `current.tmp`) — is a
    /// foreign/bogus marketplace we must never silently replace (spec §10).
    ///
    /// The original check instead compared the candidate's *parent* directory
    /// to `engineDir`, which was too broad in two ways (Ruling 54 minor 4):
    /// it accepted `engine/0.10.0.partial` and `engine/current.tmp` (same
    /// parent, not real engine roots), and it compared a symlink-*resolved*
    /// candidate against an *unresolved* `engineDir`, which falsely refuses a
    /// symlinked home. Recomputing the canonical path from the candidate's
    /// own manifest version (`layout.engineRoot(version:)`) and comparing
    /// resolved-to-resolved sidesteps both: a `.partial` dir's canonical root
    /// never has the `.partial` suffix, so it never matches even when its
    /// manifest is fully written.
    private func isManagedMarketplace(path: String) -> Bool {
        // Compare `.path` strings, not `URL` equality: `URL(fileURLWithPath:)`
        // (legacy init, used for the untrusted `path`) decides the directory
        // hint via `lstat` — a symlink is never "a directory" to it — while
        // `.appending(path:)` (used throughout `EngineLayout`) infers it via
        // `stat`, which follows the link. The *same* `current` symlink can
        // therefore come back with or without a trailing slash depending on
        // which API built the URL, and `URL.==` treats that as a different
        // path even though `.path` does not.
        let raw = URL(fileURLWithPath: path).standardizedFileURL
        let current = layout.currentEngineLink.standardizedFileURL
        if raw.path == current.path { return true }
        let resolvedRaw = raw.resolvingSymlinksInPath().standardizedFileURL
        let resolvedCurrent = current.resolvingSymlinksInPath().standardizedFileURL
        if resolvedRaw.path == resolvedCurrent.path { return true }
        // A manifest version is untrusted input from whatever directory is
        // being checked — reject anything that isn't a valid `EngineVersion`
        // (Ruling 58) before it ever reaches `engineRoot(version:)`.
        // `engineRoot` builds its path with `.appending(path:)`, which
        // honours `..` path components; an unvalidated version string like
        // `"../../evil"` lets a crafted manifest walk the computed canonical
        // root back onto the foreign directory itself, making it compare
        // equal to `resolvedRaw` and falsely pass as "ours".
        guard let manifestVersion = EngineLocator.version(atRoot: resolvedRaw), EngineVersion(manifestVersion) != nil else { return false }
        let resolvedCanonicalRoot = layout.engineRoot(version: manifestVersion).resolvingSymlinksInPath().standardizedFileURL
        return resolvedRaw.path == resolvedCanonicalRoot.path
    }

    private func registerWithClaudeCode() async throws -> String {
        let marketplace = ClaudePluginsRegistry.scoutMarketplace(pluginsDir: layout.claudePluginsDir)
        var notes: [String] = []
        switch marketplace?.source {
        case nil:
            let add = try await runner.run(executable: claude, arguments: ClaudeCodeCLI.marketplaceAdd(path: layout.currentEngineLink), environment: [:], workingDirectory: nil)
            if add.exitCode != 0 {
                let preview = Self.preview(add.stderr, max: 300)
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
            let preview = Self.preview(result.stderr, max: 300)
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
            let preview = Self.preview(result.stderr.isEmpty ? result.stdout : result.stderr, max: 400)
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
