import Combine
import Foundation

/// Proof that the owner confirmed turning write protection off in the Run protection card's
/// confirmation dialog. `RunProtectionService.turnSandboxOff(_:)` takes one, so the only path to
/// `scoutctl sandbox off` in the app goes through that dialog. Never create one anywhere else:
/// no menu item, notification action, URL, AppleScript or Shortcut may reach it. (The app has no
/// automation surface, and `ScoutTests/Engine/RunProtectionTests.swift` fails if one appears;
/// the engine also refuses `sandbox off` from a scheduled or engine-launched run.)
struct OwnerConfirmation: Sendable {
    /// Only `RunProtectionSection`'s confirmation dialog (and the service's tests) call this.
    init(ownerConfirmedInDialog: Void) {}
}

/// Runs the engine's run-protection commands and publishes the summary for Settings ▸ Engine.
@MainActor
final class RunProtectionService: ObservableObject {
    nonisolated enum Work: Equatable, Sendable { case checking, checkingPrivacy, turningOn, turningOff }

    @Published private(set) var status: RunProtectionStatus?
    /// Why there is no summary, or why the last action failed, in plain words.
    @Published private(set) var lastError: String?
    /// What's running now; the card disables its buttons meanwhile.
    @Published private(set) var work: Work?

    private let runner: any ProcessRunner
    private let environment: [String: String]
    private let scoutctl: @MainActor () -> URL?
    private var generation = 0

    /// `scoutctl` is asked each time, so the card follows the engine `EngineHealthService` found.
    init(runner: any ProcessRunner, environment: [String: String], scoutctl: @escaping @MainActor () -> URL?) {
        self.runner = runner
        self.environment = environment
        self.scoutctl = scoutctl
    }

    /// Re-read `scoutctl protection status --json`. Read-only (it self-tests the sandbox profile, never starts claude).
    func refresh() async {
        await perform(.checking) { _ in }
    }

    /// `scoutctl gates probe --cached --json`: reuses a fresh pass, else checks again against a local
    /// stand-in endpoint (no model, no spend, never your credentials). Then refreshes.
    func checkPrivacyChecks() async {
        await perform(.checkingPrivacy) { [self] exe in
            let result = try await runner.run(executable: exe, arguments: ["gates", "probe", "--cached", "--json"],
                                              environment: environment, workingDirectory: nil)
            // Exit 3–5 is a failed or refused check: the summary says so. Only output that isn't a result is an error.
            guard result.exitCode == 0 || (try? JSONSerialization.jsonObject(with: result.stdout)) != nil else {
                throw Failure("The privacy check couldn't run: \(Self.firstLine(result.stderr))")
            }
        }
    }

    /// `scoutctl sandbox on` — always safe, so no confirmation.
    func turnSandboxOn() async {
        await perform(.turningOn) { [self] exe in try await switchSandbox(exe, "on") }
    }

    /// `scoutctl sandbox off`, after the owner confirmed the risk (see `OwnerConfirmation`).
    func turnSandboxOff(_ confirmation: OwnerConfirmation) async {
        _ = confirmation
        await perform(.turningOff) { [self] exe in try await switchSandbox(exe, "off") }
    }

    // MARK: -

    private struct Failure: Error { let message: String; init(_ m: String) { message = m } }

    private func switchSandbox(_ exe: URL, _ value: String) async throws {
        let result = try await runner.run(executable: exe, arguments: ["sandbox", value],
                                          environment: environment, workingDirectory: nil)
        guard result.exitCode == 0 else {
            throw Failure("Couldn't turn write protection \(value): \(Self.firstLine(result.stderr))")
        }
    }

    /// Run `action` (if any), then re-read the summary. Overlapping calls: only the newest publishes.
    private func perform(_ kind: Work, _ action: @escaping (URL) async throws -> Void) async {
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
            let result = try await runner.run(executable: exe, arguments: ["protection", "status", "--json"],
                                              environment: environment, workingDirectory: nil)
            guard mine == generation else { return }
            if let parsed = RunProtectionStatus.parse(result.stdout) {
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

    /// Why `protection status` gave no summary: an engine from before it, or something else.
    nonisolated static func explain(_ result: ProcessResult) -> String {
        let err = String(decoding: result.stderr, as: UTF8.self)
        if err.contains("No such command") {
            return "This engine is too old to report run protection. Update the engine to see it."
        }
        if result.exitCode == 0 {
            return "The engine's run-protection report is in a format this app doesn't read. Update the app."
        }
        return "Couldn't read run protection: \(firstLine(result.stderr))"
    }

    nonisolated static func firstLine(_ data: Data) -> String {
        let line = String(decoding: data, as: UTF8.self).split(separator: "\n").first.map(String.init) ?? ""
        return line.isEmpty ? "no details" : String(line.prefix(200))
    }
}
