import Foundation

/// Pure presentation model for Settings ▸ Engine (spec §5). Views stay thin;
/// this is what the tests pin.
nonisolated struct EngineSettingsModel: Equatable, Sendable {
    let state: EngineState
    let doctor: DoctorReport?
    let bundledVersion: String?

    init(state: EngineState, doctor: DoctorReport?, bundledVersion: String?) {
        self.state = state; self.doctor = doctor; self.bundledVersion = bundledVersion
    }

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
        guard let doctor else { return "Unknown" }
        switch doctor.severity {
        case .green: return "Healthy"
        case .yellow: return "Healthy, with warnings"
        case .red: return "Needs attention"
        }
    }
    var messages: [String] {
        if case .broken(_, let reason) = state { return [reason] }
        guard let doctor else { return [] }
        return doctor.errors + doctor.warnings
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
