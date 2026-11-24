import Combine
import Foundation

/// The owner's out-of-vault answer, as `scoutctl engagement consent --show` prints it.
nonisolated enum ConsentAnswer: Equatable, Sendable {
    /// Not read yet, or the engine couldn't say.
    case unknown
    case notAsked
    case yes
    case no

    /// The `--show` mapping: the engine prints exactly `yes`, `no` or `not asked`. Anything else is `unknown`.
    static func parse(_ stdout: Data) -> ConsentAnswer {
        switch String(decoding: stdout, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines) {
        case "yes": return .yes
        case "no": return .no
        case "not asked": return .notAsked
        default: return .unknown
        }
    }
}

/// Runs the owner's decisions on the Changes tab through `scoutctl`, each bound to what the owner saw.
/// The app never writes the owner store, a pending governance file or a brain file itself: this class is the
/// only place the tab runs a decision, and `argv(for:)` is its allow-list (a source-scan test pins it).
///
/// A decision made here is recorded with `--surface app`. The engine refuses an owner decision while a
/// scheduled run of the vault is live; that refusal is worded for the owner below.
@MainActor
final class ChangesDecisions: ObservableObject {
    nonisolated enum Command: Equatable, Sendable {
        case approve(govID: String, diffHash: String)
        case reject(govID: String)
        case undo(changeID: String)
        case wrong(changeID: String)
        case consent(Bool)
        case consentShow
        case diff(changeID: String)
        case appEdit
    }

    nonisolated enum Outcome: Equatable, Sendable {
        /// Ran; the engine's stdout (trimmed, except for `diff`, which is the diff as printed).
        case done(String)
        /// The engine said no, or couldn't be run. The words are for the owner.
        case refused(String)
        /// An id or hash was malformed. Nothing was run.
        case invalid
    }

    @Published private(set) var consent: ConsentAnswer = .unknown

    /// Undos the engine recorded that no refreshed row shows applied yet. Held here, not in a view model,
    /// because this object lives as long as the app: leaving the tab (the detail's own links do) must not
    /// offer Undo again while a dreaming run is still to apply it.
    ///
    /// An id leaves the set once a refreshed row has `rolledBackBy != nil`. An undo that a dreaming run
    /// declines stays pending for the rest of the app session (accepted for v1).
    @Published private(set) var pendingUndos: Set<String> = []

    func noteUndoQueued(_ id: String) { pendingUndos.insert(id) }

    /// Drops each pending undo whose refreshed row shows it rolled back.
    func reconcilePendingUndos(with service: ChangesService) {
        guard let rows = service.snapshot?.changes.changes else { return }
        let still = DetailModel.stillPending(pendingUndos, rows: rows)
        if still != pendingUndos { pendingUndos = still }
    }

    static let liveRunMessage = "Scout is running right now. Try again when the run finishes."

    private let runner: any ProcessRunner
    private let environment: [String: String]
    private let scoutctl: @MainActor () -> URL?

    init(runner: any ProcessRunner, environment: [String: String] = [:], scoutctl: @escaping @MainActor () -> URL?) {
        self.runner = runner
        self.environment = environment
        self.scoutctl = scoutctl
    }

    func perform(_ command: Command) async -> Outcome {
        guard let args = Self.argv(for: command) else { return .invalid }
        guard let exe = scoutctl() else { return .refused("Scout's engine isn't set up yet.") }
        let result: ProcessResult
        do {
            result = try await runner.run(executable: exe, arguments: args, environment: environment, workingDirectory: nil)
        } catch {
            return .refused("Couldn't run Scout's engine.")
        }
        guard result.exitCode == 0 else { return .refused(Self.refusal(result)) }
        let out = String(decoding: result.stdout, as: UTF8.self)
        switch command {
        case .consentShow:
            consent = ConsentAnswer.parse(result.stdout)
        case .consent(let allowed):
            consent = allowed ? .yes : .no
        default:
            break
        }
        if case .diff = command { return .done(out) }
        return .done(out.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    // MARK: - The allow-list

    /// The one place a `scoutctl` argv is built for a decision, or nil when an id or hash is malformed.
    nonisolated static func argv(for command: Command) -> [String]? {
        switch command {
        case .approve(let gov, let hash):
            guard isGovID(gov), isDiffHash(hash) else { return nil }
            return ["changes", "approve", gov, "--diff-hash", hash, "--surface", "app"]
        case .reject(let gov):
            guard isGovID(gov) else { return nil }
            return ["changes", "reject", gov, "--surface", "app"]
        case .undo(let id):
            guard isChangeID(id) else { return nil }
            return ["changes", "undo", id, "--surface", "app"]
        case .wrong(let id):
            guard isChangeID(id) else { return nil }
            return ["changes", "wrong", id, "--surface", "app"]
        case .consent(let allowed):
            return ["engagement", "consent", "--out-of-vault", allowed ? "yes" : "no"]
        case .consentShow:
            return ["engagement", "consent", "--show"]
        case .diff(let id):
            guard isChangeID(id) else { return nil }
            return ["changes", "diff", id]
        case .appEdit:
            return ["engagement", "record", "app-edit", "--surface", "app"]
        }
    }

    /// `^chg-[0-9a-f]{12}$`. Checked by hand: a regex `$` would also match before a trailing newline.
    nonisolated static func isChangeID(_ s: String) -> Bool {
        let bytes = Array(s.utf8)
        return bytes.count == 16 && bytes.starts(with: Array("chg-".utf8)) && bytes[4...].allSatisfy(isLowerHex)
    }

    /// `^gov-[a-z0-9][a-z0-9-]{0,60}$`.
    nonisolated static func isGovID(_ s: String) -> Bool {
        let bytes = Array(s.utf8)
        guard bytes.count >= 5, bytes.count <= 65, bytes.starts(with: Array("gov-".utf8)) else { return false }
        let rest = bytes[4...]
        return isLowerAlnum(rest.first!) && rest.allSatisfy { isLowerAlnum($0) || $0 == UInt8(ascii: "-") }
    }

    /// `^[0-9a-f]{64}$`.
    nonisolated static func isDiffHash(_ s: String) -> Bool {
        let bytes = s.utf8
        return bytes.count == 64 && bytes.allSatisfy(isLowerHex)
    }

    private nonisolated static func isLowerHex(_ b: UInt8) -> Bool {
        (b >= UInt8(ascii: "0") && b <= UInt8(ascii: "9")) || (b >= UInt8(ascii: "a") && b <= UInt8(ascii: "f"))
    }

    private nonisolated static func isLowerAlnum(_ b: UInt8) -> Bool {
        (b >= UInt8(ascii: "0") && b <= UInt8(ascii: "9")) || (b >= UInt8(ascii: "a") && b <= UInt8(ascii: "z"))
    }

    /// The engine's first stderr line, or the owner's wording for the live-run refusal.
    private nonisolated static func refusal(_ result: ProcessResult) -> String {
        let err = String(decoding: result.stderr, as: UTF8.self)
        if err.contains("a scheduled run may not") { return liveRunMessage }
        let first = err.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }
            .first { !$0.isEmpty }
        return first.map { String($0.prefix(200)) } ?? "The engine said no (exit \(result.exitCode))."
    }
}
