import Foundation

/// `scoutctl runs --json` (engine `scout/run_ledger.py`): the recent rows of the
/// vault's run ledger, `.scout-logs/run-outcomes.jsonl`, newest first, plus the
/// run-health report every surface shares.
///
/// A contract: `ScoutTests/Fixtures/run-ledger.json` is a byte copy of the
/// engine's generated `plugin/engine/tests/fixtures/contract/run-ledger.json`
/// (`contract.yml` compares them). Unknown keys are ignored, and every enum-like
/// value stays a `String`, so a new outcome or failure class never fails the
/// decode: it only falls back to generic wording. A bumped `schema_version`
/// is a breaking change, and `parse` refuses it.
nonisolated struct RunLedger: Equatable, Sendable, Decodable {
    /// The newest `schema_version` this build understands.
    static let supportedSchemaVersion = 1

    let schemaVersion: Int
    let generatedAt: Date?
    let since: Date?
    /// More runs matched than the engine returned (`--limit`).
    let truncated: Bool
    let runs: [RunLedgerEntry]
    /// nil when the block is missing or this build can't read it; the runs still show.
    let health: RunHealth?

    private enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", generatedAt = "generated_at", since, truncated, runs, health
    }

    init(schemaVersion: Int = RunLedger.supportedSchemaVersion, generatedAt: Date? = nil, since: Date? = nil,
         truncated: Bool = false, runs: [RunLedgerEntry], health: RunHealth?) {
        self.schemaVersion = schemaVersion; self.generatedAt = generatedAt; self.since = since
        self.truncated = truncated; self.runs = runs; self.health = health
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try c.decode(Int.self, forKey: .schemaVersion)
        generatedAt = RunLedgerTime.parse(try c.decodeIfPresent(String.self, forKey: .generatedAt))
        since = RunLedgerTime.parse(try c.decodeIfPresent(String.self, forKey: .since))
        truncated = (try? c.decodeIfPresent(Bool.self, forKey: .truncated)) ?? false
        // One row this build can't read costs that row only.
        let lossy = try c.decodeIfPresent([Lossy<RunLedgerEntry>].self, forKey: .runs) ?? []
        runs = lossy.compactMap(\.value)
        health = try? c.decodeIfPresent(RunHealth.self, forKey: .health)
    }

    enum ParseError: Error, Equatable {
        /// Not the document (an older engine without `scoutctl runs`, or an error line).
        case notALedger
        /// A newer engine whose ledger shape this build doesn't know.
        case unsupportedSchema(Int)
        /// The engine said why (`{"error": …}`), e.g. a missing vault.
        case engine(String)
    }

    static func parse(_ data: Data) -> Result<RunLedger, ParseError> {
        struct Probe: Decodable { let schema_version: Int?; let error: String? }
        guard let probe = try? JSONDecoder().decode(Probe.self, from: data) else { return .failure(.notALedger) }
        if let error = probe.error { return .failure(.engine(error)) }
        guard let version = probe.schema_version else { return .failure(.notALedger) }
        guard version <= supportedSchemaVersion else { return .failure(.unsupportedSchema(version)) }
        guard let ledger = try? JSONDecoder().decode(RunLedger.self, from: data) else { return .failure(.notALedger) }
        return .success(ledger)
    }

    /// Ledger rows by their run log's file name, the key the Control Center's
    /// log-derived runs share with the ledger. The newest row wins a duplicate.
    var entriesByLog: [String: RunLedgerEntry] {
        var out: [String: RunLedgerEntry] = [:]
        for entry in runs.reversed() { if let log = entry.log { out[log] = entry } }
        return out
    }
}

/// One row of the run ledger, as `scoutctl runs --json` shows it.
nonisolated struct RunLedgerEntry: Equatable, Sendable, Decodable, Identifiable {
    let slot: String
    let mode: String
    /// scout | dreaming | research; nil for rows from older runners.
    let runner: String?
    /// schedule | heartbeat | manual; nil for older runners.
    let trigger: String?
    let startedAt: Date
    let finishedAt: Date
    let durationSeconds: Int?
    /// The run's length in awake time (sleep doesn't count).
    let awakeSeconds: Int?
    let exitCode: Int
    /// ok | failed | skipped_*; anything else reads as failed.
    let outcome: String
    /// Why: ok, a failure class (timeout, rate_limited, …) or a skip's cause.
    let failureClass: String?
    /// When a usage limit lifts (rate_limited only).
    let resetsAt: Date?
    /// enforced | unavailable | off | conflict | failed; nil for older runners.
    let sandbox: String?
    /// When sandbox is off: env | switch.
    let sandboxSource: String?
    /// clean | restored | unrestored | error | unchecked; nil for older runners.
    let integrity: String?
    let agentRuntime: String?
    let model: String?
    /// The run log's file name under `.scout-logs/` (never a path).
    let log: String?
    let commitsInWindow: Int?
    let newestCommit: String?

    var id: String { log ?? "\(slot)@\(finishedAt.timeIntervalSince1970)" }

    private enum CodingKeys: String, CodingKey {
        case slot, mode, runner, trigger, outcome, sandbox, integrity, model, log
        case startedAt = "started_at", finishedAt = "finished_at", durationSeconds = "duration_s"
        case awakeSeconds = "awake_s", exitCode = "exit_code", failureClass = "failure_class"
        case resetsAt = "resets_at", sandboxSource = "sandbox_source", agentRuntime = "agent_runtime"
        case commitsInWindow = "commits_in_window", newestCommit = "newest_commit"
    }

    init(slot: String, mode: String = "scout", runner: String? = "scout", trigger: String? = "schedule",
         startedAt: Date, finishedAt: Date, durationSeconds: Int? = nil, awakeSeconds: Int? = nil,
         exitCode: Int = 0, outcome: String, failureClass: String? = "ok", resetsAt: Date? = nil,
         sandbox: String? = nil, sandboxSource: String? = nil, integrity: String? = nil,
         agentRuntime: String? = nil, model: String? = nil, log: String? = nil,
         commitsInWindow: Int? = nil, newestCommit: String? = nil) {
        self.slot = slot; self.mode = mode; self.runner = runner; self.trigger = trigger
        self.startedAt = startedAt; self.finishedAt = finishedAt
        self.durationSeconds = durationSeconds; self.awakeSeconds = awakeSeconds; self.exitCode = exitCode
        self.outcome = outcome; self.failureClass = failureClass; self.resetsAt = resetsAt
        self.sandbox = sandbox; self.sandboxSource = sandboxSource; self.integrity = integrity
        self.agentRuntime = agentRuntime; self.model = model; self.log = log
        self.commitsInWindow = commitsInWindow; self.newestCommit = newestCommit
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func date(_ key: CodingKeys) throws -> Date {
            let raw = try c.decode(String.self, forKey: key)
            guard let d = RunLedgerTime.parse(raw) else {
                throw DecodingError.dataCorruptedError(forKey: key, in: c, debugDescription: "not an ISO time: \(raw)")
            }
            return d
        }
        slot = try c.decode(String.self, forKey: .slot)
        mode = (try c.decodeIfPresent(String.self, forKey: .mode)) ?? slot
        runner = try c.decodeIfPresent(String.self, forKey: .runner)
        trigger = try c.decodeIfPresent(String.self, forKey: .trigger)
        startedAt = try date(.startedAt)
        finishedAt = try date(.finishedAt)
        durationSeconds = try c.decodeIfPresent(Int.self, forKey: .durationSeconds)
        awakeSeconds = try c.decodeIfPresent(Int.self, forKey: .awakeSeconds)
        exitCode = try c.decode(Int.self, forKey: .exitCode)
        outcome = try c.decode(String.self, forKey: .outcome)
        failureClass = try c.decodeIfPresent(String.self, forKey: .failureClass)
        resetsAt = RunLedgerTime.parse(try c.decodeIfPresent(String.self, forKey: .resetsAt))
        sandbox = try c.decodeIfPresent(String.self, forKey: .sandbox)
        sandboxSource = try c.decodeIfPresent(String.self, forKey: .sandboxSource)
        integrity = try c.decodeIfPresent(String.self, forKey: .integrity)
        agentRuntime = try c.decodeIfPresent(String.self, forKey: .agentRuntime)
        model = try c.decodeIfPresent(String.self, forKey: .model)
        log = try c.decodeIfPresent(String.self, forKey: .log)
        commitsInWindow = try c.decodeIfPresent(Int.self, forKey: .commitsInWindow)
        newestCommit = try c.decodeIfPresent(String.self, forKey: .newestCommit)
    }

    /// The Control Center status this row means. The ledger is the record of a
    /// finished run, so it overrides whatever the log's lines suggested.
    var runStatus: RunStatus {
        switch outcome {
        case "ok": return .success
        case "skipped_lock": return .skippedConcurrency
        case "skipped_budget": return .skippedBudget
        case "skipped_gates": return .skippedGates
        case "skipped_sandbox": return .skippedSandbox
        case "skipped_runtime": return .skippedRuntime
        case "skipped_lease": return .skippedLease
        case "skipped_redundant": return .skippedRedundant
        default:
            switch failureClass {
            case "timeout": return .timeout
            case "rate_limited": return .rateLimited
            default: return .failure
            }
        }
    }

    var isSkip: Bool { outcome.hasPrefix("skipped_") }

    /// Worth the owner's attention: failed, skipped for safety, ran without
    /// write protection, or had a protected file put back.
    var needsAttention: Bool {
        if outcome != "ok" && !["skipped_lock", "skipped_redundant", "skipped_budget", "skipped_lease"].contains(outcome) {
            return true
        }
        return RunOutcomeDisplay.sandboxIsConcern(sandbox) || RunOutcomeDisplay.integrityIsConcern(integrity)
    }
}

/// The ledger's timestamps: `2026-11-16T08:00:00Z`.
nonisolated enum RunLedgerTime {
    static func parse(_ raw: String?) -> Date? {
        guard let raw else { return nil }
        return try? Date(raw, strategy: .iso8601)
    }
}

/// Decodes `T`, or nil when that one element doesn't decode.
private nonisolated struct Lossy<T: Decodable & Sendable>: Decodable, Sendable {
    let value: T?
    init(from decoder: Decoder) throws { value = try? T(from: decoder) }
}
