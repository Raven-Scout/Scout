import Foundation

/// What the user can do today about an engine state the app can't fix itself:
/// a sentence plus a value to copy (a Terminal command or a Claude Code slash
/// command). Part B ships no Install/Repair buttons — the app never runs these
/// itself; Part C replaces the row with real buttons.
nonisolated struct EngineNextStep: Equatable, Sendable {
    let text: String
    let copyValue: String
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

    /// Today's real remedy for the states that gate the tabs; nil otherwise.
    var nextStep: EngineNextStep? {
        switch state {
        case .notInstalled:
            return EngineNextStep(
                text: "Install the engine: run this in Terminal, then run /scout-setup in Claude Code.",
                copyValue: "curl -fsSL https://raw.githubusercontent.com/Raven-Scout/scout-plugin/main/install.sh | bash")
        case .broken:
            return EngineNextStep(text: "Repair the engine by running /scout-update in Claude Code.", copyValue: "/scout-update")
        case .managed(_, vaultBootstrapped: false):
            return EngineNextStep(text: "Set up your vault by running /scout-setup in Claude Code.", copyValue: "/scout-setup")
        case .managed, .external:
            return nil
        }
    }

    private var isBehindBundled: Bool {
        guard let bundled = bundledVersion, let installed = state.install?.version,
              let b = EngineVersion(bundled), let i = EngineVersion(installed) else { return false }
        return i < b
    }
    var canUpdate: Bool { state.isManaged && isBehindBundled }
    var canRepair: Bool { if case .external = state { return false }; return true }
    var showsHandOff: Bool { if case .external = state { return isBehindBundled }; return false }
}
