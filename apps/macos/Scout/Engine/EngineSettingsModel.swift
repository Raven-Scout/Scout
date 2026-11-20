import Foundation

/// The onboarding flow Settings ▸ Engine offers for a state that gates the
/// tabs (Ruling 41): "Set up…" when nothing (or no vault) is set up yet,
/// "Repair…" when the engine that was here is broken. Both open the same
/// onboarding flow in a sheet.
nonisolated enum EngineSetupAction: Equatable, Sendable {
    case setUp, repair

    var buttonTitle: String { self == .setUp ? "Set up…" : "Repair…" }
    var rowTitle: String { self == .setUp ? "Set up Scout" : "Repair engine" }
}

/// What Settings ▸ Engine offers a managed, set-up engine; every one runs
/// `AppState.runEngineUpgrade()` (the idempotent upgrade steps against the
/// bundled release). Ruling 41 / Ruling 69 I1, I4.
nonisolated enum EngineUpgradeAction: Equatable, Sendable {
    /// Older than the bundled engine.
    case update
    /// At the bundled version, but an earlier upgrade's switch never
    /// finished (`EngineUpgrader.hasUnfinishedSwitch`).
    case finishUpdate
    /// At the bundled version with a red doctor.
    case repair

    var buttonTitle: String {
        switch self { case .update: return "Update"; case .finishUpdate: return "Finish update"; case .repair: return "Repair…" }
    }
    var rowTitle: String {
        switch self { case .update: return "Update engine"; case .finishUpdate: return "Finish engine update"; case .repair: return "Repair engine" }
    }
}

/// One severity's worth of doctor findings in Settings ▸ Engine.
nonisolated struct DoctorFindingGroup: Equatable, Sendable {
    nonisolated enum Severity: Int, CaseIterable, Comparable, Sendable {
        case problem, warning, note
        static func < (a: Self, b: Self) -> Bool { a.rawValue < b.rawValue }
        var title: String {
            switch self { case .problem: "Problems"; case .warning: "Warnings"; case .note: "Notes" }
        }
        var singular: String {
            switch self { case .problem: "problem"; case .warning: "warning"; case .note: "note" }
        }
    }
    let severity: Severity
    let items: [DoctorFindingItem]
}

/// One doctor finding: an install error or warning (summary only), or a
/// run-health finding with its fix, the failing run's own line and its log.
nonisolated struct DoctorFindingItem: Equatable, Sendable, Identifiable {
    let id: String
    let summary: String
    var fix: String? = nil
    var detail: String? = nil
    var logURL: URL? = nil
    /// The files a grouped warning stands for, listed behind a disclosure (UI audit R1).
    var items: [String] = []
}

/// Pure presentation model for Settings ▸ Engine (spec §5). Views stay thin;
/// this is what the tests pin.
nonisolated struct EngineSettingsModel: Equatable, Sendable {
    let state: EngineState
    let doctor: DoctorReport?
    /// `EngineHealthService.lastError` — why the doctor produced no report.
    let lastError: String?
    let bundledVersion: String?
    /// `EngineUpgrader.hasUnfinishedSwitch` for this state (Ruling 69 I1).
    let unfinishedSwitch: Bool

    init(state: EngineState, doctor: DoctorReport?, lastError: String?, bundledVersion: String?, unfinishedSwitch: Bool = false) {
        self.state = state; self.doctor = doctor; self.lastError = lastError; self.bundledVersion = bundledVersion
        self.unfinishedSwitch = unfinishedSwitch
    }

    /// The engine is usable (tabs not gated) but its doctor could not be run
    /// or its output wasn't a report.
    private var doctorFailed: Bool { !state.gatesTabs && doctor == nil && lastError != nil }

    var sourceLabel: String {
        switch state {
        case .notInstalled: return "Not installed"
        case .broken: return "Broken"
        case .managed: return "App-managed"
        case .external(_, let source):
            switch source {
            case .devCheckout: return "Dev checkout (~/scout-plugin)"
            case .marketplaceCache, .claudeCode: return "Claude Code marketplace"
            case .installSh: return "install.sh"
            case .shim: return "Existing install (via ~/.local/bin/scoutctl)"
            case .unknown(let who): return "External (\(who))"
            }
        }
    }

    var installedVersionLabel: String { state.install?.version ?? "—" }
    var bundledVersionLabel: String? { bundledVersion }
    var rootPath: String? { state.install?.root.path }

    /// The worse of the install's verdict and the runs': the one the Health page shows (R1, T4).
    /// nil while the tabs are gated or there is no report.
    private var verdict: DoctorReport.Severity? {
        guard !state.gatesTabs, let doctor else { return nil }
        return summary.overall(doctor: doctor.severity)
    }
    var healthIsOK: Bool { verdict.map { $0 != .red } ?? false }
    var healthLabel: String {
        if case .notInstalled = state { return "Not installed" }
        if case .broken = state { return "Broken" }
        if case .managed(_, let vaultBootstrapped) = state, !vaultBootstrapped { return "Scout folder not set up" }
        if doctorFailed { return "Could not run doctor" }
        guard let verdict else { return "Unknown" }
        // "1 problem · 3 warnings" once read "Healthy, with warnings": a problem never reads as healthy (T4).
        return HealthSummary.verdictLabel(verdict)
    }
    /// The Health row's colour, as the status bar colours the same state (`ScoutStatus`):
    /// the verdict's for a report; amber while setup is unfinished; red for a broken engine
    /// or a doctor that couldn't run; grey while the first check is still out.
    var healthTone: ScoutStatus.Tone {
        if let verdict { return HealthSummary.tone(verdict) }
        switch state {
        case .notInstalled, .managed(_, vaultBootstrapped: false): return .warn
        case .broken: return .err
        case .managed, .external: return doctorFailed ? .err : .neutral
        }
    }

    /// The "Runs" row (spec §4.3) appears only when the engine reported run health.
    var showsRuns: Bool { doctor?.runHealth != nil }
    /// A Claude usage limit is holding the runs; it lifts on its own (#424).
    var runsIsPaused: Bool { runsFinding?.id == "runs.paused" }
    var runsLabel: String {
        guard let rh = doctor?.runHealth else { return "Unknown" }
        if runsIsPaused { return "Paused" }
        switch rh.severity {
        case .green: return "Healthy"
        case .yellow: return "Warnings"
        case .red: return "Failing"
        }
    }
    /// "Failing" is red, as the Health page paints a run that needs fixing (T4); a pause is grey.
    var runsTone: ScoutStatus.Tone {
        guard let rh = doctor?.runHealth else { return .neutral }
        return runsIsPaused ? .neutral : HealthSummary.tone(rh.severity)
    }
    var runsHelp: String {
        guard let rh = doctor?.runHealth else { return "" }
        if let f = rh.firstActionable {
            if runsIsPaused, let lifts = f.until.flatMap({ ISO8601DateFormatter().date(from: $0) }) {
                return "Claude usage limit: Scout resumes \(lifts.formatted(Self.resumeStyle)) (\(Self.relative(lifts)))."
            }
            return f.summary
        }
        if let ok = rh.lastOkAt { return "Last successful run \(Self.relative(ok))" }
        return "No runs recorded yet"
    }
    /// What the failing run itself printed, e.g. Claude Code's "You've hit your weekly limit · resets …" (#445).
    var runsDetail: String? { runsFinding?.detail }
    var runsFix: String? { runsFinding.flatMap { $0.fix.isEmpty ? nil : $0.fix } }
    var runsLogURL: URL? { runsFinding?.log.map { URL(fileURLWithPath: $0) } }
    private var runsFinding: RunHealth.Finding? { doctor?.runHealth?.firstActionable }
    private static let resumeStyle = Date.FormatStyle().weekday(.abbreviated).month(.abbreviated).day().hour().minute()
    private static func relative(_ date: Date) -> String {
        RelativeDateTimeFormatter().localizedString(for: date, relativeTo: Date())
    }
    private static func relative(_ iso: String) -> String {
        guard let date = ISO8601DateFormatter().date(from: iso) else { return iso }
        return relative(date)
    }

    /// What every health surface shares (UI audit R1); empty while the tabs are gated.
    var summary: HealthSummary { HealthSummary(doctor: state.gatesTabs ? nil : doctor) }

    /// Every finding the doctor reported, install and run health together,
    /// grouped worst first (card C3: Settings used to show only the first).
    /// Empty groups are left out. A run-health severity this build doesn't
    /// know is shown as a warning rather than hidden; warnings the engine
    /// groups are one item with their files (R1).
    var findingGroups: [DoctorFindingGroup] {
        guard doctor != nil, !state.gatesTabs else { return [] }
        let summary = summary
        return DoctorFindingGroup.Severity.allCases.compactMap { severity in
            let items = summary.issues(severity).map { issue in
                DoctorFindingItem(id: issue.id, summary: issue.title, fix: issue.fix, detail: issue.detail,
                                  logURL: issue.log.map { URL(fileURLWithPath: $0) }, items: issue.items)
            }
            return items.isEmpty ? nil : DoctorFindingGroup(severity: severity, items: items)
        }
    }

    /// "2 problems · 1 warning", for the Health row; nil when there is nothing.
    /// The problem and warning counts are the ones every other surface shows.
    var findingCountsLabel: String? {
        let summary = summary
        let notes = summary.noteCount
        let parts = [summary.attentionLabel, notes > 0 ? "\(notes) note\(notes == 1 ? "" : "s")" : nil].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    var messages: [String] {
        if case .broken(_, let reason) = state { return [reason] }
        if doctorFailed, let lastError { return [lastError] }
        guard let doctor else { return [] }
        return doctor.errors + doctor.warnings
    }

    /// The onboarding flow for each state that gates the tabs; nil for a
    /// usable engine (managed and set up, or external).
    var setupAction: EngineSetupAction? {
        switch state {
        case .notInstalled, .managed(_, vaultBootstrapped: false): return .setUp
        case .broken: return state.isBrokenOutsideApp ? nil : .repair   // someone else's: hand off (Ruling 69 I6)
        case .managed, .external: return nil
        }
    }

    /// One line under the Set up / Repair row.
    var setupHelp: String? {
        switch state {
        case .notInstalled: return "Install the engine that ships with this app and create your Scout folder."
        case .managed(_, vaultBootstrapped: false): return "The engine is installed; finish setting up your Scout folder."
        case .broken: return state.isBrokenOutsideApp ? nil : "Reinstall the engine that ships with this app and set up your Scout folder again."
        case .managed, .external: return nil
        }
    }

    private var isBehindBundled: Bool {
        guard let bundled = bundledVersion, let installed = state.install?.version,
              let b = EngineVersion(bundled), let i = EngineVersion(installed) else { return false }
        return i < b
    }
    private var isAtBundled: Bool {
        guard let bundled = bundledVersion, let installed = state.install?.version,
              let b = EngineVersion(bundled), let i = EngineVersion(installed) else { return false }
        return i == b
    }

    /// The upgrade-backed action for an app-managed, set-up engine (a vault
    /// that isn't set up finishes onboarding first). Never offered for an
    /// engine newer than the bundled one — that would be a downgrade.
    var upgradeAction: EngineUpgradeAction? {
        guard case .managed(_, vaultBootstrapped: true) = state else { return nil }
        if isBehindBundled { return .update }
        guard isAtBundled else { return nil }
        if unfinishedSwitch { return .finishUpdate }
        if doctor?.severity == .red { return .repair }
        return nil
    }

    /// Exactly when the launch-time upgrade applies (`AppState.shouldAutoUpgrade`):
    /// behind the bundle, or an unfinished switch at the bundled version.
    var canUpdate: Bool { upgradeAction == .update || upgradeAction == .finishUpdate }

    /// External engines — and a broken one another installer manages — are
    /// never modified (spec §10): they update through Claude Code, so the row
    /// hands the user `/scout-update` to run.
    var showsHandOff: Bool {
        if state.isBrokenOutsideApp { return true }
        if case .external = state { return isBehindBundled && !updatesAutomatically }
        return false
    }

    /// An external engine that updates itself (`scoutctl self-update run`, run-health
    /// spec §7.8): its doctor reports an `update.last` note. The "Updates" row replaces
    /// the `/scout-update` hand-off. App-managed engines never record one.
    var updatesAutomatically: Bool {
        guard case .external = state else { return false }
        return doctor?.runHealth?.findings.contains { $0.id == "update.last" } ?? false
    }
    /// A failed or held update outranks the routine "Last checked" line.
    var updatesHelp: String {
        let findings = doctor?.runHealth?.findings ?? []
        if let problem = findings.first(where: { $0.id == "update.failed" || $0.id == "update.held" }) {
            return problem.summary
        }
        return findings.first { $0.id == "update.last" }?.summary ?? ""
    }
    var updatesIsOK: Bool {
        !(doctor?.runHealth?.findings.contains { $0.id == "update.failed" || $0.id == "update.held" } ?? false)
    }
    var handOffTitle: String { state.isBrokenOutsideApp ? "Repair engine" : "Update available" }
    var handOffHelp: String {
        if state.isBrokenOutsideApp, let owner = state.install?.managedBy {
            return "This engine is managed outside the app (\(owner)). Run `/scout-update` in Claude Code to repair it."
        }
        return "This engine is managed outside the app. Run `/scout-update` in Claude Code."
    }

    /// The Scout folder field's help: scheduled runs follow the folder the
    /// engine was set up for, which only `/scout-update` changes for an
    /// engine managed in Claude Code.
    var vaultHelp: String {
        let base = "The folder Scout reads and writes. Use Default reads `~/Scout`, or the folder the engine was set up for. Points the app at a Scout folder; never moves data. Takes effect after restarting Scout. Scheduled runs keep the folder the engine was set up for"
        if case .external = state { return base + " until you run /scout-update in Claude Code." }
        return base + "."
    }
}
