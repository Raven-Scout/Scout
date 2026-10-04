import Testing
import Foundation
@testable import Scout

@Suite("EngineRelease")
struct EngineReleaseTests {
    // Fixtures load by name, not by subfolder — the test bundle flattens
    // resources, so a `Fixtures/engine` subpath does not resolve there.
    static let fixtureURL = Bundle(for: FixtureAnchor.self).url(forResource: "engine-release-fixture", withExtension: "json")!

    @Test func decodesThePin() throws {
        let data = try Data(contentsOf: Self.fixtureURL)
        let r = try JSONDecoder().decode(EngineRelease.self, from: data)
        #expect(r.schemaVersion == 1)
        #expect(r.engine.repo == "Raven-Scout/scout-plugin")
        #expect(r.engine.tag == "v\(r.engine.version)")
        #expect(r.engine.commit.count == 40)
        #expect(r.uv.version == "0.12.1")
        #expect(r.uv.sha256["aarch64-apple-darwin"]?.count == 64)
        #expect(r.tarballName == "scout-engine-\(r.engine.version).tar.gz")
    }

    /// The real pin in the app bundle must be internally consistent, and when
    /// the build phase bundled a tarball its manifest must match the pin. In CI
    /// the tarball is required; a Debug build without network may lack it.
    ///
    /// CI detection reads `ProcessInfo.processInfo.environment["CI"]`, but
    /// `xcodebuild test` only forwards `TEST_RUNNER_`-prefixed variables into
    /// the xctest host process — a plain `CI=true` set on the `xcodebuild`
    /// invocation (or ambiently by the runner) never reaches here. CI must
    /// set `TEST_RUNNER_CI=true` (see .github/workflows/ci.yml's "Run
    /// ScoutTests" step) for this guard to actually fire.
    @Test func bundledPinIsSelfConsistent() throws {
        let release = try EngineRelease.load(bundle: .main)
        #expect(release.engine.tag == "v\(release.engine.version)")
        #expect(release.engine.commit.range(of: "^[0-9a-f]{40}$", options: .regularExpression) != nil)
        guard let tarball = release.bundledTarballURL(bundle: .main) else {
            #expect(ProcessInfo.processInfo.environment["CI"] != "true", "CI builds must bundle the engine tarball")
            return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        p.arguments = ["-xzOf", tarball.path, ".claude-plugin/plugin.json"]
        let pipe = Pipe(); p.standardOutput = pipe
        try p.run(); p.waitUntilExit()
        struct Manifest: Decodable { let version: String }
        let manifest = try JSONDecoder().decode(Manifest.self, from: pipe.fileHandleForReading.readDataToEndOfFile())
        #expect(manifest.version == release.engine.version)
    }
}
