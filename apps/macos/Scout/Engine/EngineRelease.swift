import Foundation

/// The engine this build of the app ships (spec §6). Decoded from
/// `Resources/engine-release.json`; the tarball named by `tarballName` is a
/// build product placed beside it by `scripts/bundle-engine.sh`.
nonisolated struct EngineRelease: Codable, Equatable, Sendable {
    nonisolated struct Engine: Codable, Equatable, Sendable { let repo: String; let version: String; let tag: String; let commit: String }
    nonisolated struct Uv: Codable, Equatable, Sendable { let version: String; let sha256: [String: String] }

    let schemaVersion: Int
    let engine: Engine
    let uv: Uv

    enum CodingKeys: String, CodingKey { case schemaVersion = "schema_version", engine, uv }

    var tarballName: String { "scout-engine-\(engine.version).tar.gz" }

    nonisolated struct MissingResource: Error { let name: String }

    static func load(bundle: Bundle = .main) throws -> EngineRelease {
        guard let url = bundle.url(forResource: "engine-release", withExtension: "json") else { throw MissingResource(name: "engine-release.json") }
        return try JSONDecoder().decode(EngineRelease.self, from: Data(contentsOf: url))
    }

    /// nil when this build carries no engine (Debug without a reachable source).
    func bundledTarballURL(bundle: Bundle = .main) -> URL? {
        bundle.url(forResource: "scout-engine-\(engine.version)", withExtension: "tar.gz")
    }
}
