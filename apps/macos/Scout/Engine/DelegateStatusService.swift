import Combine
import Foundation

/// Runs the engine's delegate-mirror commands for Settings ▸ Agent copies (#550) and publishes
/// the status. It only runs what the CLI already offers the owner: `delegate status --json`
/// (read-only), `delegate preview <id> --json` (read-only, no network) and `delegate sync <id>
/// --json` (what the schedule tick runs every 15 minutes anyway). Linking, unlinking, deploy
/// keys, the push token and re-signing the state stay in Terminal; the card reads no key, token
/// or state file itself.
///
/// Every `scoutctl delegate` command in the app goes through `runDelegate(_:_:)`, whose
/// `Command` is the allow-list; `DelegateStatusTests` fails on the quoted word delegate anywhere else.
@MainActor
final class DelegateStatusService: ObservableObject {
    nonisolated enum Work: Equatable, Sendable {
        case checking
        case previewing(String)
        case syncing(String)
    }

    /// The `scoutctl delegate` subcommands the app may run, and no others.
    nonisolated enum Command: String, Sendable, CaseIterable {
        case status, preview, sync
    }

    /// The last status read. A later read that fails keeps it and says why in `lastError`.
    @Published private(set) var status: DelegateStatus?
    /// Why there is no status, or why the last action or read failed, in plain words.
    @Published private(set) var lastError: String?
    /// What's running now; the card disables its buttons meanwhile.
    @Published private(set) var work: Work?
    /// The last Preview or Sync now result per delegate: counts only.
    @Published private(set) var results: [String: Outcome] = [:]

    nonisolated enum Outcome: Equatable, Sendable {
        case preview(DelegateMirrorCounts)
        case synced(DelegateMirrorCounts)
        /// The engine's refusal code for that command; the refreshed status says why in fixed words.
        case refused(Command, String)
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
            results[id] = try Self.outcome(try await runDelegate(.preview, id, exe), .preview) { .preview($0) }
        }
    }

    /// `scoutctl delegate sync <id> --json`: push a new copy now if the vault changed what the agent may see.
    func sync(_ id: String) async {
        guard DelegateStatus.isDelegateID(id), canSync(id) else { return }
        await perform(.syncing(id)) { [self] exe in
            results[id] = try Self.outcome(try await runDelegate(.sync, id, exe), .sync) { .synced($0) }
        }
    }

    // MARK: -

    private struct Failure: Error { let message: String; init(_ m: String) { message = m } }
    private struct Refused: Decodable { let ok: Bool; let code: String }
    /// What `delegate status --json` printed before the card: the raw link state.
    private struct Legacy: Decodable { let ok: Bool; let links: [AnyDecodable] }
    private struct AnyDecodable: Decodable { init(from decoder: Decoder) throws {} }

    private func canSync(_ id: String) -> Bool { status?.delegates.first { $0.id == id }?.canSync == true }
    private func canPreview(_ id: String) -> Bool { status?.delegates.first { $0.id == id }?.canPreview == true }

    /// The one place the app runs `scoutctl delegate …`.
    private func runDelegate(_ command: Command, _ id: String?, _ exe: URL) async throws -> ProcessResult {
        if let id, !DelegateStatus.isDelegateID(id) { throw Failure("That isn't a delegate id Scout accepts.") }
        let args = ["delegate", command.rawValue] + (id.map { [$0] } ?? []) + ["--json"]
        return try await runner.run(executable: exe, arguments: args, environment: environment, workingDirectory: nil)
    }

    /// The counts of a preview or sync, or its refusal code. Never its message or notes: those can name vault paths.
    nonisolated static func outcome(_ result: ProcessResult, _ command: Command,
                                    _ make: (DelegateMirrorCounts) -> Outcome) throws -> Outcome {
        if result.exitCode == 0, let counts = DelegateMirrorCounts.parse(result.stdout) { return make(counts) }
        if let refused = try? JSONDecoder().decode(Refused.self, from: result.stdout), !refused.ok,
           DelegateStatus.isRefusalCode(refused.code) {
            return .refused(command, refused.code)
        }
        throw Failure("The \(command.rawValue) didn't finish. Run scoutctl delegate \(command.rawValue) in Terminal to see why.")
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
            let result = try await runDelegate(.status, nil, exe)
            guard mine == generation else { return }
            if let parsed = DelegateStatus.parse(result.stdout) {
                status = parsed
                lastError = actionError
            } else if status == nil, Self.isNothingToShow(result) {
                // An engine from before the card with no link, or before delegates: there is no card to show.
                lastError = actionError
            } else {
                lastError = actionError ?? Self.explain(result)   // keeps the last good status, if any
            }
        } catch {
            guard mine == generation else { return }
            lastError = actionError ?? "Couldn't run scoutctl: \(String(describing: error).prefix(160))"
        }
    }

    /// An older engine that has nothing to show: no `delegate` command, or the old status with no link.
    nonisolated static func isNothingToShow(_ result: ProcessResult) -> Bool {
        if String(decoding: result.stderr, as: UTF8.self).contains("No such command") { return true }
        guard let legacy = try? JSONDecoder().decode(Legacy.self, from: result.stdout) else { return false }
        return legacy.ok && legacy.links.isEmpty
    }

    /// Why `delegate status --json` gave no status: an engine from before it, or something else.
    nonisolated static func explain(_ result: ProcessResult) -> String {
        if result.exitCode == 0 || (try? JSONSerialization.jsonObject(with: result.stdout)) != nil {
            // An engine before the versioned status printed a different JSON shape.
            return "This engine is too old to report agent copies in a form this app reads. Update the engine to see them."
        }
        return "Couldn't read agent copies: \(RunProtectionService.firstLine(result.stderr))"
    }
}
