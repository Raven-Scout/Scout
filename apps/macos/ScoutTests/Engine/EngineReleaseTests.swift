import Testing
import Foundation
@testable import Scout

@Suite("EngineRelease")
struct EngineReleaseTests {
    // Fixtures load by name, not by subfolder — the test bundle flattens
    // resources, so a `Fixtures/engine` subpath does not resolve there.
    static let fixtureURL = Bundle(for: FixtureAnchor.self).url(forResource: "engine-release-fixture", withExtension: "json")!

    /// The app bundle: ScoutTests runs hosted in Scout.app.
    static var app: Bundle { Bundle(for: AppState.self) }

    @Test func decodesTheGeneratedShape() throws {
        let data = try Data(contentsOf: Self.fixtureURL)
        let r = try JSONDecoder().decode(EngineRelease.self, from: data)
        #expect(r.schemaVersion == 2)
        #expect(r.version == "9.9.9")
        #expect(r.engine.version == "9.9.9")
        #expect(r.engine.commit?.count == 40)
        #expect(r.uv.version == "0.12.1")
        #expect(r.uv.sha256["aarch64-apple-darwin"]?.count == 64)
        #expect(r.tarballName == "scout-engine-9.9.9.tar.gz")
    }

    /// The build commit is diagnostics only; a release without one still decodes.
    @Test func theCommitIsOptional() throws {
        let json = #"{"schema_version": 2, "version": "1.2.3", "engine": {"version": "1.2.3"}, "uv": {"version": "0.12.1", "sha256": {}}}"#
        let r = try JSONDecoder().decode(EngineRelease.self, from: Data(json.utf8))
        #expect(r.engine == .init(version: "1.2.3", commit: nil))
    }

    /// Bundling is deterministic and needs no network (spec §5), so EVERY
    /// build — Debug, CI, Release — carries the generated engine-release.json
    /// and the tarball it names, and the tarball's manifest agrees with it.
    @Test func everyBuildBundlesTheEngine() throws {
        let release = try EngineRelease.load(bundle: Self.app)
        #expect(release.schemaVersion == 2)
        #expect(release.version == release.engine.version)
        // Agreement with EngineVersion's own validity rules (SemVer §9): a
        // version bundle-engine.sh's regex accepts but EngineVersion rejects
        // (e.g. a leading-zero pre-release identifier) would make
        // EngineUpgrader.needsUpgrade silently always false.
        #expect(EngineVersion(release.version) != nil, "bundled version \"\(release.version)\" must also parse as an EngineVersion")
        #expect(release.engine.commit?.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil)
        let tarball = try #require(release.bundledTarballURL(bundle: Self.app), "the build must bundle \(release.tarballName)")
        #expect(try Self.manifestVersion(inTarball: tarball) == release.engine.version)
        // Final review C1: the archive root must be a Claude Code DIRECTORY
        // marketplace, or `claude plugin marketplace add engine/current`
        // fails on every clean Mac. bundle-engine.sh generates it from the
        // repo-root manifest: named scout-plugin (the foreign-source check
        // keys on it), one plugin, scout, from "./" at the bundled version.
        let marketplace = try JSONDecoder().decode(BundledMarketplace.self, from: Self.extract(".claude-plugin/marketplace.json", fromTarball: tarball))
        #expect(marketplace.name == ClaudePluginsRegistry.scoutMarketplaceName)
        #expect(marketplace.plugins.map(\.name) == ["scout"])
        #expect(marketplace.plugins.first?.source == "./")
        #expect(marketplace.plugins.first?.version == release.engine.version)
    }

    private struct BundledMarketplace: Decodable {
        struct Plugin: Decodable { let name: String; let source: String; let version: String? }
        let name: String
        let plugins: [Plugin]
    }

    /// One version for Scout (spec D2 / §7): the bundled plugin.json version,
    /// the generated engine-release.json's top-level `version` (which
    /// `release.sh finalize` checks) and the app's own
    /// `CFBundleShortVersionString` (`MARKETING_VERSION`) are one string.
    @Test func bundledPluginVersionEqualsTheAppVersion() throws {
        let release = try EngineRelease.load(bundle: Self.app)
        let tarball = try #require(release.bundledTarballURL(bundle: Self.app))
        let appVersion = try #require(Self.app.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String)
        let manifestVersion = try Self.manifestVersion(inTarball: tarball)
        let hint = "plugin/.claude-plugin/plugin.json and MARKETING_VERSION (apps/macos/Scout.xcodeproj) must move together (spec D2): bump both, e.g. `versioning set X.Y.Z` plus MARKETING_VERSION"
        #expect(manifestVersion == appVersion, "\(hint)")
        #expect(release.version == appVersion, "\(hint)")
        #expect(release.version == manifestVersion, "\(hint)")
    }

    /// The uv pin rides through bundling unchanged: the generated file's `uv`
    /// is the checked-in `Scout/Resources/uv-release.json`.
    @Test func bundledUvPinIsTheCheckedInPin() throws {
        let release = try EngineRelease.load(bundle: Self.app)
        let pinURL = try #require(Self.app.url(forResource: "uv-release", withExtension: "json"))
        let pin = try JSONDecoder().decode(EngineRelease.Uv.self, from: Data(contentsOf: pinURL))
        #expect(release.uv == pin)
        for arch in ["aarch64-apple-darwin", "x86_64-apple-darwin"] {
            #expect(pin.sha256[arch]?.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil, "\(arch)")
        }
    }

    private static func manifestVersion(inTarball tarball: URL) throws -> String {
        struct Manifest: Decodable { let version: String }
        return try JSONDecoder().decode(Manifest.self, from: extract(".claude-plugin/plugin.json", fromTarball: tarball)).version
    }

    private static func extract(_ member: String, fromTarball tarball: URL) throws -> Data {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        p.arguments = ["-xzOf", tarball.path, member]
        let pipe = Pipe(); p.standardOutput = pipe
        try p.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return data
    }
}
