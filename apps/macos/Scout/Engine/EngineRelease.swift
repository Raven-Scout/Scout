import Foundation

/// The engine this build of the app ships (unified-release spec §5). Decoded
/// from `Resources/engine-release.json`, which `scripts/bundle-engine.sh`
/// GENERATES at build time — engine version from `plugin/.claude-plugin/
/// plugin.json`, the build commit for diagnostics, and the checked-in uv pin
/// (`Scout/Resources/uv-release.json`) — beside the tarball named by
/// `tarballName` (a `git archive HEAD:plugin` of the same commit).
nonisolated struct EngineRelease: Codable, Equatable, Sendable {
    nonisolated struct Engine: Codable, Equatable, Sendable {
        let version: String
        /// The commit the app was built from. Diagnostics only: the bundled
        /// plugin/ tree is that commit's, but nothing installs by commit.
        let commit: String?

        init(version: String, commit: String? = nil) {
            self.version = version
            self.commit = commit
        }
    }
    nonisolated struct Uv: Codable, Equatable, Sendable { let version: String; let sha256: [String: String] }

    let schemaVersion: Int
    /// Scout's one version (spec D2): plugin.json's, which is also the app's
    /// `CFBundleShortVersionString`. `scripts/release.sh finalize` reads this
    /// top-level field and refuses a release whose app version differs.
    /// Always equal to `engine.version`.
    let version: String
    let engine: Engine
    let uv: Uv

    enum CodingKeys: String, CodingKey { case schemaVersion = "schema_version", version, engine, uv }

    var tarballName: String { "scout-engine-\(engine.version).tar.gz" }

    nonisolated struct MissingResource: Error { let name: String }

    static func load(bundle: Bundle = .main) throws -> EngineRelease {
        guard let url = bundle.url(forResource: "engine-release", withExtension: "json") else { throw MissingResource(name: "engine-release.json") }
        return try JSONDecoder().decode(EngineRelease.self, from: Data(contentsOf: url))
    }

    /// nil when the bundle carries no tarball for this version (a broken
    /// build: bundling is deterministic and needs no network, so every build
    /// that produced engine-release.json also produced the tarball).
    func bundledTarballURL(bundle: Bundle = .main) -> URL? {
        bundle.url(forResource: "scout-engine-\(engine.version)", withExtension: "tar.gz")
    }
}
