import Combine
import Foundation

/// Runs the engine's delegate-mirror commands for Settings ▸ Agent copies (#550) and publishes
/// the status. It only runs what the CLI already offers the owner: `delegate status --json`
/// (read-only), `delegate preview <id> --json` (read-only, no network) and `delegate sync <id>
/// --json` (what the schedule tick runs every 15 minutes anyway). Linking, unlinking, deploy
/// keys, the push token and re-signing the state stay in Terminal; the app reads no key, token
/// or state file itself.
@MainActor
final class DelegateStatusService: ObservableObject {
    nonisolated enum Work: Equatable, Sendable {
        case checking
        case previewing(String)
        case syncing(String)
    }

    @Published private(set) var status: DelegateStatus?
    /// Why there is no status, or why the last action failed, in plain words.
    @Published private(set) var lastError: String?
    /// What's running now; the card disables its buttons meanwhile.
    @Published private(set) var work: Work?
    /// The last Preview or Sync now result per delegate: counts only.
    @Published private(set) var results: [String: Outcome] = [:]

    nonisolated enum Outcome: Equatable, Sendable {
        case preview(DelegateMirrorCounts)
        case synced(DelegateMirrorCounts)
        /// The engine's refusal code; the refreshed status says why in fixed words.
        case refused(String)
    }

    private let runner: any ProcessRunner
    private let environment: [String: String]
    private let scoutctl: @MainActor () -> URL?
    private var generation = 0

    init(runner: any ProcessRunner, environment: [String: String], scoutctl: @escaping @MainActor () -> URL?) {
        self.runner = runner
        self.environment = environment
        self.scoutctl = scoutctl
    }

    /// Re-read `scoutctl delegate status --json`. Read-only: what the card runs on its own.
    func refresh() async {
        await perform(.checking) { _ in }
    }

    /// `scoutctl delegate preview <id> --json`: what a sync would put in the copy. Writes nothing; no network.
    func preview(_ id: String) async {
        guard DelegateStatus.isDelegateID(id), canPreview(id) else { return }
        await perform(.previewing(id)) { [self] exe in
            let result = try await runner.run(executable: exe, arguments: ["delegate", "preview", id, "--json"],
                                              environment: environment, workingDirectory: nil)
            results[id] = try Self.outcome(result, as: { .preview($0) }, what: "preview")
        }
    }

    /// `scoutctl delegate sync <id> --json`: push a new copy now if the vault changed what the agent may see.
    func sync(_ id: String) async {
        guard DelegateStatus.isDelegateID(id), canSync(id) else { return }
        await perform(.syncing(id)) { [self] exe in
            let result = try await runner.run(executable: exe, arguments: ["delegate", "sync", id, "--json"],
                                              environment: environment, workingDirectory: nil)
            results[id] = try Self.outcome(result, as: { .synced($0) }, what: "sync")
        }
    }

    // MARK: -

    private struct Failure: Error { let message: String; init(_ m: String) { message = m } }
    private struct Refused: Decodable { let ok: Bool; let code: String }

    private func canSync(_ id: String) -> Bool { status?.delegates.first { $0.id == id }?.canSync == true }
    private func canPreview(_ id: String) -> Bool { status?.delegates.first { $0.id == id }?.canPreview == true }

    /// The counts of a preview or sync, or its refusal code. Never its message or notes: those can name vault paths.
    nonisolated static func outcome(_ result: ProcessResult, as make: (DelegateMirrorCounts) -> Outcome,
                                    what: String) throws -> Outcome {
        if result.exitCode == 0, let counts = DelegateMirrorCounts.parse(result.stdout) { return make(counts) }
        if let refused = try? JSONDecoder().decode(Refused.self, from: result.stdout), !refused.ok,
           refused.code.range(of: #"^[a-z][a-z_]{0,47}$"#, options: .regularExpression) != nil {
            return .refused(refused.code)
        }
        throw Failure("The \(what) didn't finish. Run scoutctl delegate \(what) in Terminal to see why.")
    }

    /// Run `action`, then re-read the status. Overlapping calls: only the newest publishes.
    private func perform(_ kind: Work, _ action: @escaping @MainActor (URL) async throws -> Void) async {
        generation += 1
        let mine = generation
        guard let exe = scoutctl() else {
            status = nil
            lastError = "Scout's engine isn't set up yet."
            return
        }
        work = kind
        defer { if mine == generation { work = nil } }
        var actionError: String?
        do { try await action(exe) } catch let f as Failure { actionError = f.message } catch {
            actionError = "Couldn't run scoutctl: \(String(describing: error).prefix(160))"
        }
        do {
            let result = try await runner.run(executable: exe, arguments: ["delegate", "status", "--json"],
                                              environment: environment, workingDirectory: nil)
            guard mine == generation else { return }
            if let parsed = DelegateStatus.parse(result.stdout) {
                status = parsed
                lastError = actionError
            } else {
                status = nil
                lastError = actionError ?? Self.explain(result)
            }
        } catch {
            guard mine == generation else { return }
            status = nil
            lastError = actionError ?? "Couldn't run scoutctl: \(String(describing: error).prefix(160))"
        }
    }

    /// Why `delegate status --json` gave no status: an engine from before it, or something else.
    nonisolated static func explain(_ result: ProcessResult) -> String {
        let err = String(decoding: result.stderr, as: UTF8.self)
        if err.contains("No such command") {
            return "This engine is too old to report agent copies. Update the engine to see them."
        }
        if result.exitCode == 0 || (try? JSONSerialization.jsonObject(with: result.stdout)) != nil {
            // An engine before the versioned status printed a different JSON shape.
            return "The engine's agent-copies report is in a format this app doesn't read. Update the engine or the app."
        }
        return "Couldn't read agent copies: \(RunProtectionService.firstLine(result.stderr))"
    }
}
