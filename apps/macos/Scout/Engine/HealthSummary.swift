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
///
/// The engine ranks its warnings by risk (`warning_triage`, UI audit R13): warnings
/// list high risk first, and each carries what the owner needs to mark it reviewed.
/// A reviewed warning is in `reviewed`, never in `issues` or any count.
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
        /// The engine's risk for an install warning; nil for anything else or an older engine.
        var risk: DoctorReport.Risk? = nil
        /// Why it has that risk.
        var riskReason: String? = nil
        /// What marking it reviewed (or showing it again) takes; nil when it can't be reviewed.
        var review: Review? = nil
        /// When the owner reviewed it (only in `reviewed`).
        var reviewedAt: Date? = nil
    }

    /// The warning `scoutctl bootstrap review-warning` acts on, at the fingerprint the owner saw.
    nonisolated struct Review: Equatable, Sendable, Hashable {
        let id: String
        let fingerprint: String
    }

    /// Problems, then warnings, then notes; within each, scheduled runs before the install,
    /// and install warnings highest risk first.
    let issues: [Issue]
    /// Warnings the owner marked reviewed, highest risk first. They count for nothing.
    let reviewed: [Issue]

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
        let triage = doctor?.warningTriage ?? []
        let open = Dictionary(triage.filter { $0.reviewedAt == nil }.map { ($0.summary, $0) },
                              uniquingKeysWith: { a, _ in a })
        for (i, text) in (doctor?.warnings ?? []).enumerated() {
            var issue: Issue
            if let entry = open[text] {
                issue = Self.issue(kind: entry.kind, summary: text, items: entry.items, id: "warning.\(i)")
                issue.risk = entry.risk
                issue.riskReason = entry.reason
                issue.review = Review(id: entry.id, fingerprint: entry.fingerprint)
            } else if let group = groups[text] {
                issue = Self.issue(for: group, id: "warning.\(i)")
            } else {
                issue = Issue(id: "warning.\(i)", severity: .warning, source: .install, title: text)
            }
            issues.append(issue)
        }
        let order: (Issue.Source) -> Int = { $0 == .runs ? 0 : 1 }
        let rank: (Issue) -> Int = { ($0.risk ?? .medium).rawValue }
        self.issues = issues.enumerated().sorted { a, b in
            (a.element.severity, order(a.element.source), rank(a.element), a.offset)
                < (b.element.severity, order(b.element.source), rank(b.element), b.offset)
        }.map(\.element)
        self.reviewed = triage.enumerated().compactMap { i, entry in
            guard let at = entry.reviewedAt else { return nil }
            var issue = Self.issue(kind: entry.kind, summary: entry.summary, items: entry.items, id: "reviewed.\(i)")
            issue.risk = entry.risk
            issue.riskReason = entry.reason
            issue.review = Review(id: entry.id, fingerprint: entry.fingerprint)
            issue.reviewedAt = at
            return issue
        }
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
        issue(kind: group.kind, summary: group.summary, items: group.items, id: id)
    }

    /// A privacy warning the app knows (grouped, or one file of a kind) gets a short title and its
    /// fix; any other keeps the engine's text. The risk chip says how much it matters (R13).
    static func issue(kind: String, summary: String, items: [String], id: String) -> Issue {
        let n = items.count
        let title: String, fix: String?
        switch kind {
        case "privacy.script-raw-kb-read":
            title = "\(n) vault script\(n == 1 ? " reads" : "s read") the knowledge base directly, without Scout's privacy labels"
            fix = "Have each read through `scoutctl kb files` and `scoutctl kb read`, so a private note never reaches a shared channel."
        case "privacy.script-raw-kb-read.internal":
            title = "\(n) vault script\(n == 1 ? " reads" : "s read") the knowledge base directly, but \(n == 1 ? "mentions" : "mention") no way to send what \(n == 1 ? "it reads" : "they read")"
            fix = "Check \(n == 1 ? "it only feeds" : "they only feed") checks inside the vault, then mark this reviewed. Or have \(n == 1 ? "it" : "them") read through `scoutctl kb files`."
        case "privacy.runner-no-reader":
            title = "\(n) vault runner\(n == 1 ? " doesn't" : "s don't") mark their reads as unattended"
            fix = "Add `export SCOUT_MODE=\"<run-name>\"` and `export SCOUT_READER=autonomous` to each before it starts Claude."
        default:
            title = summary
            fix = nil
        }
        return Issue(id: id, severity: .warning, source: .install, title: title, fix: fix, items: items)
    }
}
