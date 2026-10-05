import Foundation

nonisolated struct SessionProject: Codable, Equatable, Hashable, Sendable {
    let key: String
    let name: String
    let groupID: String?
    /// Keyed by `AgentSessionState.rawValue`.
    let counts: [String: Int]

    enum CodingKeys: String, CodingKey {
        case key, name, counts
        case groupID = "group_id"
    }
}

nonisolated struct SessionSourceError: Codable, Equatable, Hashable, Sendable {
    let source: String
    let message: String
}

/// Display-only settings the engine echoes from `agent_sessions:` so the app
/// never parses the vault's YAML (spec §4.11).
nonisolated struct SessionIndexDisplay: Codable, Equatable, Hashable, Sendable {
    let doneVisibleHours: Int
    let staleAfterDays: Int

    enum CodingKeys: String, CodingKey {
        case doneVisibleHours = "done_visible_hours"
        case staleAfterDays = "stale_after_days"
    }
}

nonisolated enum SessionIndexError: Error, Equatable {
    /// The file declares a schema this build does not know. The service keeps
    /// the last good index and asks for a scout-plugin update.
    case unsupportedSchema(Int)
    case malformed(String)
}

/// `.scout-cache/sessions-index.json`, schema v1 (spec §4.8). The contract is
/// the engine's `test_index_json_contract_key_sets`.
nonisolated struct SessionIndex: Equatable, Sendable {
    static let supportedSchemaVersion = 1

    let schemaVersion: Int
    let generatedAt: Date?
    let sourceCounts: [String: Int]
    let sourceErrors: [SessionSourceError]
    let display: SessionIndexDisplay
    let projects: [SessionProject]
    let sessions: [AgentSession]
    /// Entries in `sessions` that did not decode. They are skipped, so one
    /// odd record cannot blank the page, and counted, so the skip is visible.
    let unreadableSessions: Int

    /// Everything but `generatedAt`, which changes on every build. The service
    /// republishes only when this differs.
    func hasSameContent(as other: SessionIndex) -> Bool {
        schemaVersion == other.schemaVersion
            && sessions == other.sessions
            && projects == other.projects
            && sourceErrors == other.sourceErrors
            && display == other.display
            && unreadableSessions == other.unreadableSessions
    }

    static func decode(_ data: Data) throws -> SessionIndex {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            guard let date = parseTimestamp(text) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "not an ISO-8601 timestamp: \(text)")
            }
            return date
        }
        let header: Header
        do {
            header = try decoder.decode(Header.self, from: data)
        } catch {
            throw SessionIndexError.malformed(String(String(describing: error).prefix(200)))
        }
        guard header.schemaVersion == supportedSchemaVersion else {
            throw SessionIndexError.unsupportedSchema(header.schemaVersion)
        }
        let body: Body
        do {
            body = try decoder.decode(Body.self, from: data)
        } catch {
            throw SessionIndexError.malformed(String(String(describing: error).prefix(200)))
        }
        let sessions = body.sessions.compactMap(\.value)
        return SessionIndex(
            schemaVersion: header.schemaVersion,
            generatedAt: body.generatedAt,
            sourceCounts: body.sourceCounts,
            sourceErrors: body.sourceErrors,
            display: body.display,
            projects: body.projects,
            sessions: sessions,
            unreadableSessions: body.sessions.count - sessions.count
        )
    }

    /// The engine writes `2026-09-15T12:00:00Z`; GitHub's `updatedAt`, passed
    /// through, has the same shape. Fractional seconds are accepted too.
    static func parseTimestamp(_ text: String) -> Date? {
        if let date = try? Date(text, strategy: .iso8601) { return date }
        let fractional = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
        return try? Date(text, strategy: fractional)
    }

    private struct Header: Decodable {
        let schemaVersion: Int
        enum CodingKeys: String, CodingKey { case schemaVersion = "schema_version" }
    }

    private struct Body: Decodable {
        let generatedAt: Date?
        let sourceCounts: [String: Int]
        let sourceErrors: [SessionSourceError]
        let display: SessionIndexDisplay
        let projects: [SessionProject]
        let sessions: [Lossy<AgentSession>]

        enum CodingKeys: String, CodingKey {
            case display, projects, sessions
            case generatedAt = "generated_at"
            case sourceCounts = "source_counts"
            case sourceErrors = "source_errors"
        }
    }

    /// Decodes an element, or nil when it does not decode.
    private struct Lossy<Wrapped: Decodable>: Decodable {
        let value: Wrapped?
        init(from decoder: Decoder) throws {
            value = try? Wrapped(from: decoder)
        }
    }
}
