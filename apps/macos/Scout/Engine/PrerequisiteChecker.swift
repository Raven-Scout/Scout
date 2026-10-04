import Foundation

nonisolated enum ClaudeStatus: Equatable, Sendable {
    case missing
    case installed(path: URL, version: String?)
}

nonisolated enum AuthState: Equatable, Sendable {
    case unknown, signedOut, signedIn
}

nonisolated enum ToolState: Equatable, Sendable {
    case missing
    case present(URL)
}

nonisolated struct Prerequisites: Equatable, Sendable {
    let claude: ClaudeStatus
    let auth: AuthState
    let git: ToolState
    let uv: ToolState

    /// Claude Code *installed* is the only hard gate (spec §4.3) — auth and
    /// the git/uv toolchain are surfaced to the user but don't block kicking
    /// off the install flow.
    var canInstallEngine: Bool {
        if case .installed = claude { return true }
        return false
    }
}

/// Probes the four things the engine needs from the machine (spec §4.3).
/// Never shells out to `/usr/bin/git` to detect its presence — a missing
/// Command Line Tools install makes that pop Apple's install dialog — so git
/// presence is inferred from `xcode-select -p` succeeding instead, and a
/// Homebrew/MacPorts install is accepted as a fallback.
nonisolated struct PrerequisiteChecker: Sendable {
    let runner: any ProcessRunner
    let layout: EngineLayout
    let claudePathOverride: String
    let resolveClaude: @Sendable (String) -> String?
    /// Injectable so tests never depend on what this Mac or CI runner has
    /// installed at these real paths (Ruling 49). Production uses the real
    /// candidate paths; every test passes `[]`.
    let gitCandidates: [String]
    let uvCandidates: [String]

    static let defaultGitCandidates = ["/opt/homebrew/bin/git", "/usr/local/bin/git", "/opt/local/bin/git"]
    static let defaultUvCandidates = ["/opt/homebrew/bin/uv", "/usr/local/bin/uv"]

    /// `ClaudeLauncher.resolveClaudePath` is MainActor-isolated (the app
    /// target defaults to MainActor isolation; `ClaudeLauncher` carries no
    /// `nonisolated` override and none is added here). `check()` always
    /// invokes `resolveClaude` from inside `await MainActor.run`, so
    /// `assumeIsolated` below is a sound bridge, not a guess — this closure
    /// must never be called from anywhere else.
    static let defaultResolveClaude: @Sendable (String) -> String? = { override in
        MainActor.assumeIsolated {
            ClaudeLauncher.resolveClaudePath(override: override)
        }
    }

    init(
        runner: any ProcessRunner,
        layout: EngineLayout,
        claudePathOverride: String = "",
        resolveClaude: @escaping @Sendable (String) -> String? = PrerequisiteChecker.defaultResolveClaude,
        gitCandidates: [String] = PrerequisiteChecker.defaultGitCandidates,
        uvCandidates: [String] = PrerequisiteChecker.defaultUvCandidates
    ) {
        self.runner = runner
        self.layout = layout
        self.claudePathOverride = claudePathOverride
        self.resolveClaude = resolveClaude
        self.gitCandidates = gitCandidates
        self.uvCandidates = uvCandidates
    }

    func check() async -> Prerequisites {
        let claudePath = await MainActor.run { resolveClaude(claudePathOverride) }
        var claude: ClaudeStatus = .missing
        var auth: AuthState = .unknown
        if let claudePath {
            let url = URL(fileURLWithPath: claudePath)
            let version = (try? await runner.run(executable: url, arguments: ClaudeCodeCLI.version, environment: [:], workingDirectory: nil))
                .flatMap { ClaudeCodeCLI.parseVersion($0.stdout) }
            claude = .installed(path: url, version: version)
            if let status = try? await runner.run(executable: url, arguments: ClaudeCodeCLI.authStatus, environment: [:], workingDirectory: nil),
               let parsed = ClaudeCodeCLI.parseAuthStatus(status.stdout) {
                auth = parsed.loggedIn ? .signedIn : .signedOut
            }
        }
        return Prerequisites(claude: claude, auth: auth, git: await gitState(), uv: uvState())
    }

    private func gitState() async -> ToolState {
        if let result = try? await runner.run(executable: URL(fileURLWithPath: "/usr/bin/xcode-select"), arguments: ["-p"], environment: [:], workingDirectory: nil),
           result.exitCode == 0 {
            return .present(URL(fileURLWithPath: "/usr/bin/git"))
        }
        if let path = gitCandidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) {
            return .present(URL(fileURLWithPath: path))
        }
        return .missing
    }

    private func uvState() -> ToolState {
        if FileManager.default.isExecutableFile(atPath: layout.uvURL.path) {
            return .present(layout.uvURL)
        }
        if let path = uvCandidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) {
            return .present(URL(fileURLWithPath: path))
        }
        return .missing
    }
}
