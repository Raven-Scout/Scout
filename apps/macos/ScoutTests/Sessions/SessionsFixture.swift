import Foundation
@testable import Scout

/// `Fixtures/sessions-index.fixture.json`: eleven anonymised sessions covering
/// every state, both PR shapes, a CLI-only session, a sub-agent and one of
/// Scout's own runs. Every timestamp is relative to `now`.
enum SessionsFixture {
    /// The fixture's `generated_at`.
    static let now = Date(timeIntervalSince1970: 1_789_473_600)  // 2026-09-15T12:00:00Z

    static func data() throws -> Data {
        let bundle = Bundle(for: FixtureAnchor.self)
        guard let url = bundle.url(forResource: "sessions-index.fixture", withExtension: "json")
                ?? bundle.resourceURL?.appendingPathComponent("sessions-index.fixture.json") else {
            throw CocoaError(.fileNoSuchFile)
        }
        return try Data(contentsOf: url)
    }

    static func index() throws -> SessionIndex {
        try SessionIndex.decode(data())
    }

    /// The fixture as a mutable JSON object, for tests that need a variant.
    static func object() throws -> [String: Any] {
        try JSONSerialization.jsonObject(with: data()) as? [String: Any] ?? [:]
    }

    static func encode(_ object: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: object)
    }

    static func session(_ id: String, in index: SessionIndex) -> AgentSession? {
        index.sessions.first { $0.id == id }
    }
}
