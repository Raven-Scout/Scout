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

    init(severity: Severity, errors: [String], warnings: [String], runHealth: RunHealth? = nil) {
        self.severity = severity; self.errors = errors; self.warnings = warnings; self.runHealth = runHealth
    }

    private enum CodingKeys: String, CodingKey { case severity, errors, warnings, runHealth = "run_health" }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        severity = try c.decode(Severity.self, forKey: .severity)
        errors = try c.decode([String].self, forKey: .errors)
        warnings = try c.decode([String].self, forKey: .warnings)
        // A run_health this build can't decode (a future severity, a changed
        // shape) costs only that block — never the install health beside it.
        runHealth = try? c.decodeIfPresent(RunHealth.self, forKey: .runHealth)
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
