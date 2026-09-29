import Foundation

/// `scoutctl bootstrap doctor` result. Engines ≥ 0.10.0 emit JSON (`--json`);
/// older adopted engines print `severity: …` / `warning: …` / `error: …`
/// lines, which the app still understands so adoption works before upgrade.
nonisolated struct DoctorReport: Equatable, Sendable, Decodable {
    nonisolated enum Severity: String, Decodable, Sendable { case green, yellow, red }
    let severity: Severity
    let errors: [String]
    let warnings: [String]

    static func parse(stdout: Data) -> DoctorReport? {
        if let json = try? JSONDecoder().decode(DoctorReport.self, from: stdout) { return json }
        guard let text = String(data: stdout, encoding: .utf8) else { return nil }
        var severity: Severity?
        var errors: [String] = [], warnings: [String] = []
        for raw in text.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("severity: ") { severity = Severity(rawValue: String(line.dropFirst("severity: ".count))) }
            else if line.hasPrefix("warning: ") { warnings.append(String(line.dropFirst("warning: ".count))) }
            else if line.hasPrefix("error: ") { errors.append(String(line.dropFirst("error: ".count))) }
        }
        guard let severity else { return nil }
        return DoctorReport(severity: severity, errors: errors, warnings: warnings)
    }
}
