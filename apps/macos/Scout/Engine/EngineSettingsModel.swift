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

/// Pure presentation model for Settings ▸ Engine (spec §5). Views stay thin;
/// this is what the tests pin.
nonisolated struct EngineSettingsModel: Equatable, Sendable {
    let state: EngineState
    let doctor: DoctorReport?
    /// `EngineHealthService.lastError` — why the doctor produced no report.
    let lastError: String?
    let bundledVersion: String?

    init(state: EngineState, doctor: DoctorReport?, lastError: String?, bundledVersion: String?) {
        self.state = state; self.doctor = doctor; self.lastError = lastError; self.bundledVersion = bundledVersion
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
        case .broken: return .repair
        case .managed, .external: return nil
        }
    }

    /// One line under the Set up / Repair row.
    var setupHelp: String? {
        switch state {
        case .notInstalled: return "Install the engine that ships with this app and create your vault."
        case .managed(_, vaultBootstrapped: false): return "The engine is installed; finish setting up your vault."
        case .broken: return "Reinstall the engine that ships with this app and re-run vault setup."
        case .managed, .external: return nil
        }
    }

    private var isBehindBundled: Bool {
        guard let bundled = bundledVersion, let installed = state.install?.version,
              let b = EngineVersion(bundled), let i = EngineVersion(installed) else { return false }
        return i < b
    }
    /// An app-managed, set-up engine older than the bundled one — exactly
    /// when the launch-time upgrade applies (`AppState.shouldAutoUpgrade`).
    /// A vault that isn't set up finishes onboarding first.
    var canUpdate: Bool {
        guard case .managed(_, vaultBootstrapped: true) = state else { return false }
        return isBehindBundled
    }
    /// External engines are never modified (spec §10): they update through
    /// Claude Code, so the row hands the user `/scout-update` to run.
    var showsHandOff: Bool { if case .external = state { return isBehindBundled }; return false }
}
