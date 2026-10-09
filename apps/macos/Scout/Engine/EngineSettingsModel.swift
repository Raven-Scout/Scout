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

    var healthIsOK: Bool {
        guard !state.gatesTabs, case .some(let d) = doctor else { return false }
        return d.severity != .red
    }
    var healthLabel: String {
        if case .notInstalled = state { return "Not installed" }
        if case .broken = state { return "Broken" }
        if case .managed(_, let vaultBootstrapped) = state, !vaultBootstrapped { return "Vault not set up" }
        if doctorFailed { return "Could not run doctor" }
        guard let doctor else { return "Unknown" }
        switch doctor.severity {
        case .green: return "Healthy"
        case .yellow: return "Healthy, with warnings"
        case .red: return "Needs attention"
        }
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
        case .notInstalled: return "Install the engine that ships with this app and create your vault."
        case .managed(_, vaultBootstrapped: false): return "The engine is installed; finish setting up your vault."
        case .broken: return state.isBrokenOutsideApp ? nil : "Reinstall the engine that ships with this app and re-run vault setup."
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
        if case .external = state { return isBehindBundled }
        return false
    }
    var handOffTitle: String { state.isBrokenOutsideApp ? "Repair engine" : "Update available" }
    var handOffHelp: String {
        if state.isBrokenOutsideApp, let owner = state.install?.managedBy {
            return "This engine is managed outside the app (\(owner)). Run `/scout-update` in Claude Code to repair it."
        }
        return "This engine is managed outside the app. Run `/scout-update` in Claude Code."
    }

    /// The Scout vault field's help: scheduled runs follow the vault the
    /// engine was set up for, which only `/scout-update` changes for an
    /// engine managed in Claude Code.
    var vaultHelp: String {
        let base = "Folder Scout reads and writes. Blank = `~/Scout`, or the vault the engine was set up for. Points the app at a vault; never moves data. Takes effect after restarting Scout. Scheduled runs keep the vault the engine was set up for"
        if case .external = state { return base + " until you run /scout-update in Claude Code." }
        return base + "."
    }
}
