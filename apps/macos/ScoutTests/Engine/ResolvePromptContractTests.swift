import Foundation
import Testing
@testable import Scout

/// "Resolve with Claude": the `scoutctl health resolve-prompt <kind> <key> --json` contract.
///
/// `resolve-prompt.json` is a byte copy of the engine's generated
/// `plugin/engine/tests/fixtures/contract/resolve-prompt.json` (root CLAUDE.md): one document per
/// kind under `finding`, `connector` and `run`. When it is regenerated, update the pins in
/// `parsesEachKind`.
@Suite("Resolve prompt contract")
struct ResolvePromptContractTests {
    private final class FixtureAnchor {}

    static let subjects: [String: ResolveSubject] = [
        "finding": .finding("run.integrity_restored"),
        "connector": .connector("mcp:example_tracker"),
        "run": .run(logName: "scout-2026-11-19_18-30.log"),
    ]

    static func fixture() throws -> [String: Any] {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: "resolve-prompt", withExtension: "json"))
        return try #require(try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
    }

    /// The fixture's document for `kind`, as the engine prints it.
    static func document(_ kind: String, editing edit: (inout [String: Any]) -> Void = { _ in }) throws -> Data {
        var doc = try #require(try fixture()[kind] as? [String: Any])
        edit(&doc)
        return try JSONSerialization.data(withJSONObject: doc)
    }

    @Test func parsesEachKind() throws {
        let titles = [
            "finding": "Investigate: a scheduled run changed a protected file",
            "connector": "Investigate: connector mcp:example_tracker is failing",
            "run": "Investigate: a morning-briefing run failed",
        ]
        for (kind, subject) in Self.subjects {
            let p = try #require(ResolvePrompt.parse(try Self.document(kind), for: subject), "\(kind)")
            #expect(p.kind == kind && p.key == subject.key)
            #expect(p.current)
            #expect(p.title == titles[kind])
            #expect(p.cwd == "/Users/alex/Scout")
            #expect(p.prompt.hasPrefix("# \(p.title)\n"))
            #expect(p.prompt.contains("## Guardrails"))
            #expect(subject.isValidKey)
        }
    }

    @Test func refusesOtherSchema() throws {
        let data = try Self.document("finding") { $0["schema_version"] = 2 }
        #expect(ResolvePrompt.parse(data, for: Self.subjects["finding"]!) == nil)
    }

    @Test func refusesMismatchedSubject() throws {
        #expect(ResolvePrompt.parse(try Self.document("finding"), for: .finding("other.id")) == nil)
        #expect(ResolvePrompt.parse(try Self.document("run"), for: .connector("scout-2026-11-19_18-30.log")) == nil)
    }

    @Test func refusesMissingKey() throws {
        let data = try Self.document("connector") { $0.removeValue(forKey: "cwd") }
        #expect(ResolvePrompt.parse(data, for: Self.subjects["connector"]!) == nil)
    }
}
