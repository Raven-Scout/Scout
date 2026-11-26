import Foundation

/// `scoutctl bootstrap doctor` result. Engines ≥ 0.10.0 emit JSON (`--json`);
/// older adopted engines print `severity: …` / `warning: …` / `error: …`
/// lines, which the app still understands so adoption works before upgrade.
nonisolated struct DoctorReport: Equatable, Sendable, Decodable {
    nonisolated enum Severity: String, Decodable, Sendable { case green, yellow, red }
    let severity: Severity
    let errors: [String]
    let warnings: [String]
    /// The run-health block (engines that emit `run_health`); nil for older
    /// engines, legacy text, a missing vault, or a block this app can't read.
    let runHealth: RunHealth?
    /// Informational lines (write protection, privacy checks, sign-in, …);
    /// empty for engines that don't emit them.
    let notes: [String]
    /// Warnings that stand for many files of one kind (UI audit R1, engine #505):
    /// each `summary` is one of `warnings`, verbatim. Empty for older engines.
    let warningGroups: [WarningGroup]
    /// Every warning ranked by risk, open or reviewed by the owner (UI audit R13). A reviewed
    /// one is in neither `warnings` nor `warningGroups`. Empty for older engines.
    let warningTriage: [TriageEntry]

    nonisolated struct WarningGroup: Equatable, Sendable, Decodable {
        /// e.g. `privacy.script-raw-kb-read`.
        let kind: String
        let summary: String
        /// The files the warning stands for (vault-relative).
        let items: [String]
    }

    /// How much a warning matters: high (privacy or run protection; a script that could send
    /// what it reads), medium, or low (a checker whose output stays in the vault).
    nonisolated enum Risk: Int, Comparable, Sendable, CaseIterable {
        case high, medium, low
        static func < (a: Risk, b: Risk) -> Bool { a.rawValue < b.rawValue }
        /// A risk this build doesn't know ranks as medium: shown, never buried.
        init(engine raw: String) {
            switch raw {
            case "high": self = .high
            case "low": self = .low
            default: self = .medium
            }
        }
        var title: String {
            switch self {
            case .high: return "High risk"
            case .medium: return "Medium risk"
            case .low: return "Low risk"
            }
        }
    }

    /// One row of `warning_triage` (engine `scout/doctor_triage.py`).
    nonisolated struct TriageEntry: Equatable, Sendable, Decodable {
        /// What `scoutctl bootstrap review-warning` takes.
        let id: String
        let kind: String
        let risk: Risk
        /// Why it has that risk, in the engine's words; nil when there's nothing to add.
        let reason: String?
        /// The warning's text, verbatim as it is (or was) in `warnings`.
        let summary: String
        let items: [String]
        /// What a review holds: the warning's files or text as the owner saw them.
        let fingerprint: String
        /// When the owner marked it reviewed; nil while it is open.
        let reviewedAt: Date?
        /// False for a warning about run protection itself (a privacy-check count, write protection):
        /// the engine never lets a review hide it. A missing key reads as false.
        let reviewable: Bool

        private enum CodingKeys: String, CodingKey {
            case id, kind, risk, reason, summary, items, fingerprint, reviewable, reviewedAt = "reviewed_at"
        }

        init(id: String, kind: String, risk: Risk, reason: String? = nil, summary: String, items: [String] = [],
             fingerprint: String, reviewedAt: Date? = nil, reviewable: Bool = true) {
            self.id = id; self.kind = kind; self.risk = risk; self.reason = reason; self.summary = summary
            self.items = items; self.fingerprint = fingerprint; self.reviewedAt = reviewedAt
            self.reviewable = reviewable
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = try c.decode(String.self, forKey: .id)
            kind = (try? c.decodeIfPresent(String.self, forKey: .kind)) ?? ""
            risk = Risk(engine: (try? c.decodeIfPresent(String.self, forKey: .risk)) ?? "")
            reason = try? c.decodeIfPresent(String.self, forKey: .reason)
            summary = try c.decode(String.self, forKey: .summary)
            items = (try? c.decodeIfPresent([String].self, forKey: .items)) ?? []
            fingerprint = try c.decode(String.self, forKey: .fingerprint)
            reviewedAt = RunLedgerTime.parse(try? c.decodeIfPresent(String.self, forKey: .reviewedAt))
            reviewable = (try? c.decodeIfPresent(Bool.self, forKey: .reviewable)) ?? false
        }
    }

    init(severity: Severity, errors: [String], warnings: [String], runHealth: RunHealth? = nil, notes: [String] = [],
         warningGroups: [WarningGroup] = [], warningTriage: [TriageEntry] = []) {
        self.severity = severity; self.errors = errors; self.warnings = warnings; self.runHealth = runHealth
        self.notes = notes; self.warningGroups = warningGroups; self.warningTriage = warningTriage
    }

    private enum CodingKeys: String, CodingKey {
        case severity, errors, warnings, notes, runHealth = "run_health", warningGroups = "warning_groups"
        case warningTriage = "warning_triage"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        severity = try c.decode(Severity.self, forKey: .severity)
        errors = try c.decode([String].self, forKey: .errors)
        warnings = try c.decode([String].self, forKey: .warnings)
        // A run_health this build can't decode (a future severity, a changed
        // shape) costs only that block — never the install health beside it.
        runHealth = try? c.decodeIfPresent(RunHealth.self, forKey: .runHealth)
        notes = (try? c.decodeIfPresent([String].self, forKey: .notes)) ?? []
        // Like run_health: a groups block this build can't read costs only the grouping.
        warningGroups = (try? c.decodeIfPresent([WarningGroup].self, forKey: .warningGroups)) ?? []
        // One entry this build can't read costs that entry only: its warning still shows, unranked.
        let triage = (try? c.decodeIfPresent([LossyTriage].self, forKey: .warningTriage)) ?? []
        warningTriage = triage.compactMap(\.value)
    }

    private nonisolated struct LossyTriage: Decodable, Sendable {
        let value: TriageEntry?
        init(from decoder: Decoder) throws { value = try? TriageEntry(from: decoder) }
    }

    /// JSON is read from `stdout` only. The legacy text form is split across
    /// both streams — the engine prints `severity:` / `warning:` lines to
    /// stdout but every `error:` line to stderr (`typer.echo(..., err=True)`)
    /// — so the text fallback reads stdout's lines, then stderr's.
    static func parse(stdout: Data, stderr: Data) -> DoctorReport? {
        if let json = try? JSONDecoder().decode(DoctorReport.self, from: stdout) { return json }
        guard let text = String(data: stdout, encoding: .utf8) else { return nil }
        let errText = String(decoding: stderr, as: UTF8.self)
        var severity: Severity?
        var errors: [String] = [], warnings: [String] = []
        for raw in text.split(separator: "\n") + errText.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("severity: ") { severity = Severity(rawValue: String(line.dropFirst("severity: ".count))) }
            else if line.hasPrefix("warning: ") { warnings.append(String(line.dropFirst("warning: ".count))) }
            else if line.hasPrefix("error: ") { errors.append(String(line.dropFirst("error: ".count))) }
        }
        guard let severity else { return nil }
        return DoctorReport(severity: severity, errors: errors, warnings: warnings)
    }
}

/// `run_health` from `scoutctl bootstrap doctor --json` (spec §4.3): are the
/// scheduled runs themselves healthy, as distinct from the install. Unknown
/// keys are ignored; `findings[].severity` stays a string so a new value
/// (beyond yellow/red/note) never fails the decode.
nonisolated struct RunHealth: Equatable, Sendable, Decodable {
    nonisolated struct Finding: Equatable, Sendable, Decodable {
        let id: String
        let severity: String
        let since: String?
        let summary: String
        let fix: String
        let source: String
        /// The failing run's own error line, verbatim (#445); engines before it omit the key.
        let detail: String?
        /// Absolute path of that run's log, when it is still on disk.
        let log: String?
        /// When a `runs.paused` usage limit lifts, ISO-8601 UTC.
        let until: String?

        init(id: String, severity: String, since: String?, summary: String, fix: String, source: String,
             detail: String? = nil, log: String? = nil, until: String? = nil) {
            self.id = id; self.severity = severity; self.since = since; self.summary = summary; self.fix = fix
            self.source = source; self.detail = detail; self.log = log; self.until = until
        }
    }
    nonisolated struct SlotStatus: Equatable, Sendable, Decodable {
        let key: String
        let type: String
        /// The newer of the slot's last dispatch and last session end — shown as "last ran".
        let lastFire: String?
        private enum CodingKeys: String, CodingKey { case key, type, lastFire = "last_fire" }
    }

    let severity: DoctorReport.Severity
    let evaluatedAt: String
    let lastOkAt: String?
    let findings: [Finding]
    let slots: [SlotStatus]

    private enum CodingKeys: String, CodingKey {
        case severity, findings, slots
        case evaluatedAt = "evaluated_at", lastOkAt = "last_ok_at"
    }

    /// The first finding worth showing; `note`s are informational.
    var firstActionable: Finding? { findings.first { $0.severity != "note" } }
}
