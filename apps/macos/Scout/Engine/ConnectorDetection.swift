import Foundation

/// One entry of `scoutctl connectors detect --json` (engine ≥ 0.10.0, spec E4):
/// `{name: {status, needs_user_input, evidence}}`.
///
/// Decoding is tolerant, so a newer engine can't break onboarding. A status
/// the app doesn't know (or a missing one) maps to `.unknown` (E4:
/// "unmappable is unknown"). A missing `needs_user_input` becomes `[]` and a
/// missing `evidence` becomes `""`. `parse` drops any entry that still can't
/// be read (e.g. not an object) instead of failing the whole map.
nonisolated struct ConnectorDetection: Decodable, Equatable, Sendable {
    nonisolated enum Status: String, Decodable, Sendable { case connected, needsAuth = "needs_auth", unavailable, unknown }
    let status: Status
    let needsUserInput: [String]
    let evidence: String

    enum CodingKeys: String, CodingKey { case status, needsUserInput = "needs_user_input", evidence }

    init(status: Status, needsUserInput: [String], evidence: String) {
        self.status = status
        self.needsUserInput = needsUserInput
        self.evidence = evidence
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // `try?` flattens the Optional from `decodeIfPresent`: a wrong type
        // and an absent key both come back nil.
        let raw = try? c.decodeIfPresent(String.self, forKey: .status)
        status = raw.flatMap(Status.init(rawValue:)) ?? .unknown
        needsUserInput = (try? c.decodeIfPresent([String].self, forKey: .needsUserInput)) ?? []
        evidence = (try? c.decodeIfPresent(String.self, forKey: .evidence)) ?? ""
    }

    /// One map value; nil when the entry isn't readable at all.
    private nonisolated struct Lenient: Decodable {
        let value: ConnectorDetection?
        init(from decoder: Decoder) throws { value = try? ConnectorDetection(from: decoder) }
    }

    /// nil only when the document isn't a JSON object of entries.
    static func parse(_ data: Data) -> [String: ConnectorDetection]? {
        guard let entries = try? JSONDecoder().decode([String: Lenient].self, from: data) else { return nil }
        return entries.compactMapValues(\.value)
    }

    /// Human labels for the connectors the shipped probe registry
    /// (`plugin/templates/connector-probes.yaml`) knows; unknown keys show as-is.
    static let displayNames: [String: String] = [
        "slack": "Slack", "calendar": "Google Calendar", "email": "Gmail", "linear": "Linear", "github": "GitHub",
        "granola": "Granola", "fathom": "Fathom", "drive": "Google Drive", "claude_sessions": "Claude Code sessions",
    ]
}
