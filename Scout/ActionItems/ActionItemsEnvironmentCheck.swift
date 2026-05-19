import Foundation

/// Result of the action-items writer environment probe.
///
/// v0.5.2 rewrite: pre-v0.5.2 this carried `python3Path` and `missingScripts`
/// because the writer shelled out to standalone Python scripts. The writer
/// now goes through `scoutctl action-items <op>`, so the only thing we need
/// to verify is that scoutctl exists AND exposes the action-items command
/// group. Old fields are kept as deprecated stubs so views that reference
/// them by name still compile during the transition; treat them as empty.
struct ActionItemsEnvironmentResult: Equatable, Sendable {
    let ok: Bool
    /// One-line diagnostic shown in the env banner when `ok == false`.
    let message: String?

    /// Convenience for view code that just wants a yes/no.
    static let okResult = ActionItemsEnvironmentResult(ok: true, message: nil)
}

/// Probes scoutctl to verify Action Items writes will work. Runs
/// `scoutctl action-items --help` and checks for a clean exit. Failure modes:
///   - scoutctl not on the resolved path (ENOENT / non-zero from /usr/bin/env)
///   - scoutctl present but doesn't expose `action-items` (very old plugin)
final class ActionItemsEnvironmentCheck: @unchecked Sendable {
    private let scoutctl: URL
    private let argumentsPrefix: [String]
    private let runner: any ProcessRunner

    init(
        scoutctl: URL,
        argumentsPrefix: [String] = [],
        runner: any ProcessRunner
    ) {
        self.scoutctl = scoutctl
        self.argumentsPrefix = argumentsPrefix
        self.runner = runner
    }

    func run() async throws -> ActionItemsEnvironmentResult {
        let probe: ProcessResult
        do {
            probe = try await runner.run(
                executable: scoutctl,
                arguments: argumentsPrefix + ["action-items", "--help"],
                environment: [:],
                workingDirectory: nil
            )
        } catch {
            return ActionItemsEnvironmentResult(
                ok: false,
                message: "scoutctl not found — install scout-plugin and re-launch."
            )
        }
        if probe.exitCode != 0 {
            let stderr = String(data: probe.stderr, encoding: .utf8) ?? ""
            let snippet = ScheduleService.previewBytes(probe.stderr, max: 120)
            let detail = snippet.isEmpty ? "exit \(probe.exitCode)" : snippet
            // Old scoutctl versions predate the action-items subcommand.
            let stale = stderr.lowercased().contains("no such command")
                || stderr.lowercased().contains("no such option")
            return ActionItemsEnvironmentResult(
                ok: false,
                message: stale
                    ? "scoutctl is too old — update scout-plugin to use Action Items writes."
                    : "scoutctl action-items unavailable: \(detail)"
            )
        }
        return .okResult
    }
}
