import Foundation

/// `scoutctl bootstrap auto --json` (engine ≥ 0.10.0, spec E3). Unknown keys
/// (e.g. `schema_version`, `dry_run`, `snapshots_recorded`, and future
/// additive fields like `mutated`/`vault_edits`) are ignored by `Decodable`
/// so the engine can grow the contract without breaking older app builds.
nonisolated struct BootstrapResult: Decodable, Equatable, Sendable {
    let action: String
    let reason: String?
    let vault: String
    let pluginVersion: String?
    let error: String?
    let doctor: DoctorReport?
    let conflicts: [String]
    let backups: [String]
    let pointer: String?

    enum CodingKeys: String, CodingKey {
        case action, reason, vault, pluginVersion = "plugin_version", error, doctor, conflicts, backups, pointer
    }

    static func parse(_ data: Data) -> BootstrapResult? { try? JSONDecoder().decode(BootstrapResult.self, from: data) }
}
