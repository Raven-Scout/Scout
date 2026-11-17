import Foundation

/// `scoutctl protection status --json` (engine `scout/run_protection.py`): what protects a
/// scheduled run — the write sandbox, the last privacy-check probe, the last run's integrity
/// check, the commit and Telegram broker, and each slot's agent runtime and limits.
///
/// A contract with the engine: `ScoutTests/Fixtures/protection-status.json` is a byte copy of
/// the engine's generated `plugin/engine/tests/fixtures/contract/protection-status.json`.
/// Status values stay strings, so a value this build doesn't know never fails the decode;
/// the presentation model (`RunProtectionModel`) says "Unknown" for it instead.
/// Paths (`log`) are relative to the vault.
nonisolated struct RunProtectionStatus: Equatable, Sendable, Decodable {
    nonisolated struct Sandbox: Equatable, Sendable, Decodable {
        /// enforced | failed | unavailable | off | conflict | not_checked | unknown
        let status: String
        let reason: String
        /// on | off — the owner's switch (`scoutctl sandbox on|off`).
        let `switch`: String
        /// default | owner
        let switchSource: String
        /// Where `status` came from: settings (off, unavailable, conflict), last_run (enforced or failed, from the
        /// newest run; the plain report never self-tests), self_test (`--check-sandbox`), this_run; nil when unknown.
        let checkedBy: String?

        private enum CodingKeys: String, CodingKey {
            case status, reason, `switch`, switchSource = "switch_source", checkedBy = "checked_by"
        }
    }

    nonisolated struct GateProbe: Equatable, Sendable, Decodable {
        /// passed | failed | unsafe | not_checked
        let status: String
        let reason: String
        let checkedAt: String?
        let claudeVersion: String?
        /// "" | endpoint | launcher — why a probe refused to run.
        let unsafe: String
        /// Older than the runners' 24-hour cache: the next run probes again.
        let stale: Bool

        private enum CodingKeys: String, CodingKey {
            case status, reason, unsafe, stale
            case checkedAt = "checked_at", claudeVersion = "claude_version"
        }
    }

    nonisolated struct LastRun: Equatable, Sendable, Decodable {
        let slot: String
        let mode: String
        let finishedAt: String
        let outcome: String
        /// enforced | unavailable | off | conflict | failed; nil from runners that predate it.
        let sandbox: String?
        let sandboxSource: String?
        /// clean | restored | unrestored | error | unchecked; nil from runners that predate it.
        let integrity: String?
        let log: String?

        private enum CodingKeys: String, CodingKey {
            case slot, mode, outcome, sandbox, integrity, log
            case finishedAt = "finished_at", sandboxSource = "sandbox_source"
        }
    }

    nonisolated struct Broker: Equatable, Sendable, Decodable {
        /// on | unavailable | off | not_recorded
        let status: String
        let reason: String
        let recordedAt: String?

        private enum CodingKeys: String, CodingKey { case status, reason, recordedAt = "recorded_at" }
    }

    nonisolated struct Slot: Equatable, Sendable, Decodable {
        let key: String
        let type: String
        let runtime: String?
        let runtimeSource: String?
        let model: String?
        let modelSource: String?
        let runTimeoutS: Int?
        /// config | default | off | owner
        let timeoutSource: String?
        let maxTokensPerDay: Int?
        let ceilingSource: String?
        /// scout-config.yaml values that would have loosened a limit, so were ignored.
        let ignored: [String]
        /// Why this slot's runtime couldn't be resolved (then the fields above are nil).
        let error: String?

        private enum CodingKeys: String, CodingKey {
            case key, type, runtime, model, ignored, error
            case runtimeSource = "runtime_source", modelSource = "model_source", runTimeoutS = "run_timeout_s"
            case timeoutSource = "timeout_source", maxTokensPerDay = "max_tokens_per_day", ceilingSource = "ceiling_source"
        }
    }

    nonisolated struct Problem: Equatable, Sendable, Decodable, Identifiable {
        let id: String
        /// yellow | red | note
        let severity: String
        let summary: String
        let fix: String
        /// turn_on_sandbox | check_sandbox | check_gates | show_log | nil
        let action: String?
        let log: String?
    }

    let schemaVersion: Int
    let evaluatedAt: String
    /// green | yellow | red
    let severity: String
    let sandbox: Sandbox
    let gateProbe: GateProbe
    let lastRun: LastRun?
    let broker: Broker
    let slots: [Slot]
    let problems: [Problem]

    private enum CodingKeys: String, CodingKey {
        case severity, sandbox, broker, slots, problems
        case schemaVersion = "schema_version", evaluatedAt = "evaluated_at", gateProbe = "gate_probe", lastRun = "last_run"
    }

    /// The schema this build reads; a newer one is refused rather than half-read.
    static let supportedSchema = 1

    static func parse(_ data: Data) -> RunProtectionStatus? {
        guard let status = try? JSONDecoder().decode(RunProtectionStatus.self, from: data),
              status.schemaVersion == supportedSchema else { return nil }
        return status
    }
}
