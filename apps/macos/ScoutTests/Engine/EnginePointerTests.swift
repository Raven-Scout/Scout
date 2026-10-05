import Testing
import Foundation
@testable import Scout

@Suite("EnginePointer")
struct EnginePointerTests {
    // Fixtures load by name, not by subfolder — the test bundle flattens
    // resources, so a `Fixtures/engine` subpath does not resolve there.
    static let fixtureURL = Bundle(for: FixtureAnchor.self).url(forResource: "engine-pointer", withExtension: "json")!

    @Test func decodesTheEngineJsonWrittenByScoutctl() throws {
        let data = try Data(contentsOf: Self.fixtureURL)
        let p = try EnginePointer.decode(data)
        #expect(p.schemaVersion == 1)
        #expect(p.version == "0.10.0")
        #expect(p.engineRoot == "/Users/alex/.local/share/scout/engine/0.10.0")
        #expect(p.scoutctl == "/Users/alex/.local/share/scout/venv/0.10.0/bin/scoutctl")
        #expect(p.vault == "/Users/alex/Scout")
        #expect(p.managedBy == "scout-app")
    }

    @Test func rejectsUnknownSchemaVersion() {
        let data = #"{"schema_version": 2, "version": "x", "engine_root": "", "python": "", "scoutctl": "", "vault": "", "managed_by": "", "written_at": ""}"#.data(using: .utf8)!
        #expect(throws: (any Error).self) { try EnginePointer.decode(data) }
    }

    @Test func loadReturnsNilForMissingOrMalformed() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(EnginePointer.load(from: dir.appendingPathComponent("missing.json")) == nil)
        let bad = dir.appendingPathComponent("bad.json")
        try "{not json".write(to: bad, atomically: true, encoding: .utf8)
        #expect(EnginePointer.load(from: bad) == nil)
    }
}
