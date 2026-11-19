import Foundation

/// One list of what's wrong with Scout, worst first, and one count of it,
/// shared by every health surface: the Health page, Settings ▸ Engine, the
/// Doctor window, the menu-bar panel and the status bar (UI audit R1). They
/// used to tell three stories ("Nothing needs fixing", "35 warnings", an
/// amber "Ready" with no reason) from the same doctor report.
///
/// Warnings the engine groups (`warning_groups`, #505) are one issue each,
/// with their files behind a disclosure, so 34 scripts that read the
/// knowledge base the same way count once, not 34 times.
nonisolated struct HealthSummary: Equatable, Sendable {
    nonisolated struct Issue: Equatable, Sendable, Identifiable {
        nonisolated enum Source: Equatable, Sendable { case runs, install }

        let id: String
        let severity: DoctorFindingGroup.Severity
        let source: Source
        /// One line, in the owner's words where the engine gives them.
        let title: String
        var fix: String? = nil
        /// A failed run's own error line.
        var detail: String? = nil
        /// The run's log, as the engine wrote it (absolute, or vault-relative from `scoutctl runs`).
        var log: String? = nil
        /// The files a grouped warning stands for.
        var items: [String] = []
        /// When a run-health finding began, ISO-8601.
        var since: String? = nil
    }

    /// Problems, then warnings, then notes; within each, scheduled runs before the install.
    let issues: [Issue]

    /// `runHealth` defaults to the doctor's own block; the Health page passes the run
    /// ledger's, evaluated by the same engine code a moment later.
    init(doctor: DoctorReport?, runHealth: RunHealth?? = nil) {
        var issues: [Issue] = []
        let findings = (runHealth ?? doctor?.runHealth)?.findings ?? []
        for (i, f) in findings.enumerated() {
            let severity: DoctorFindingGroup.Severity = switch f.severity {
            case "red": .problem
            case "note": .note
            default: .warning   // a severity this build doesn't know is shown, not hidden
            }
            issues.append(Issue(id: "\(f.id).\(i)", severity: severity, source: .runs, title: f.summary,
                                fix: f.fix.isEmpty ? nil : f.fix, detail: f.detail, log: f.log, since: f.since))
        }
        for (i, text) in (doctor?.errors ?? []).enumerated() {
            issues.append(Issue(id: "error.\(i)", severity: .problem, source: .install, title: text))
        }
        let groups = Dictionary((doctor?.warningGroups ?? []).map { ($0.summary, $0) }, uniquingKeysWith: { a, _ in a })
        for (i, text) in (doctor?.warnings ?? []).enumerated() {
            if let group = groups[text] {
                issues.append(Self.issue(for: group, id: "warning.\(i)"))
            } else {
                issues.append(Issue(id: "warning.\(i)", severity: .warning, source: .install, title: text))
            }
        }
        let order: (Issue.Source) -> Int = { $0 == .runs ? 0 : 1 }
        self.issues = issues.enumerated().sorted { a, b in
            (a.element.severity, order(a.element.source), a.offset) < (b.element.severity, order(b.element.source), b.offset)
        }.map(\.element)
    }

    var problemCount: Int { issues.filter { $0.severity == .problem }.count }
    var warningCount: Int { issues.filter { $0.severity == .warning }.count }
    var noteCount: Int { issues.filter { $0.severity == .note }.count }
    /// Problems and warnings: what "needs a look" counts everywhere.
    var attentionCount: Int { problemCount + warningCount }

    var severity: DoctorReport.Severity { problemCount > 0 ? .red : warningCount > 0 ? .yellow : .green }

    /// The one sentence saying why: the worst issue's title.
    var reason: String? { issues.first { $0.severity != .note }?.title }

    /// "1 problem · 2 warnings"; nil when nothing needs a look.
    var attentionLabel: String? {
        let parts = [(problemCount, DoctorFindingGroup.Severity.problem), (warningCount, .warning)]
            .filter { $0.0 > 0 }
            .map { n, s in "\(n) \(n == 1 ? s.singular : s.title.lowercased())" }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// Issues of one kind, for a surface that lists them in severity groups.
    func issues(_ severity: DoctorFindingGroup.Severity, from source: Issue.Source? = nil) -> [Issue] {
        issues.filter { $0.severity == severity && (source == nil || $0.source == source) }
    }

    // MARK: Grouped warnings

    /// A group the app knows gets a short title and its fix; any other keeps the engine's text.
    static func issue(for group: DoctorReport.WarningGroup, id: String) -> Issue {
        let n = group.items.count
        let title: String, fix: String?
        switch group.kind {
        case "privacy.script-raw-kb-read":
            title = "\(n) script\(n == 1 ? "" : "s") in your Scout folder \(n == 1 ? "reads" : "read") the knowledge base directly, without Scout's privacy labels"
            fix = "Have each read through `scoutctl kb files` and `scoutctl kb read`, so a private note never reaches a shared channel."
        case "privacy.runner-no-reader":
            title = "\(n) run script\(n == 1 ? "" : "s") in your Scout folder \(n == 1 ? "doesn't" : "don't") mark their reads as unattended"
            fix = "Add `export SCOUT_MODE=\"<run-name>\"` and `export SCOUT_READER=autonomous` to each before it starts Claude."
        default:
            title = group.summary
            fix = nil
        }
        return Issue(id: id, severity: .warning, source: .install, title: title, fix: fix, items: group.items)
    }
}
