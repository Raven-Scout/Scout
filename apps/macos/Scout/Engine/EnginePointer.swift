import Foundation

/// Mirror of `~/.local/state/scout/engine.json` (spec §4.2), written by every
/// `scoutctl bootstrap …`. The app only reads it.
nonisolated struct EnginePointer: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let version: String
    let engineRoot: String
    let python: String
    let scoutctl: String
    let vault: String
    let managedBy: String
    let writtenAt: String

    static let supportedSchemaVersion = 1

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", version, engineRoot = "engine_root", python, scoutctl, vault
        case managedBy = "managed_by", writtenAt = "written_at"
    }

    nonisolated struct UnsupportedSchema: Error, Equatable { let found: Int }

    static func decode(_ data: Data) throws -> EnginePointer {
        let p = try JSONDecoder().decode(EnginePointer.self, from: data)
        guard p.schemaVersion == supportedSchemaVersion else { throw UnsupportedSchema(found: p.schemaVersion) }
        return p
    }

    /// nil on missing, unreadable, malformed, or unknown schema — callers fall
    /// back to discovery (spec §4.2 "authoritative but not exclusive").
    static func load(from url: URL) -> EnginePointer? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? decode(data)
    }
}
