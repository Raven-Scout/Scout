import Foundation

/// One entry of `scoutctl connectors detect --json` (engine ≥ 0.10.0, spec E4):
/// `{name: {status, needs_user_input, evidence}}`.
nonisolated struct ConnectorDetection: Decodable, Equatable, Sendable {
    nonisolated enum Status: String, Decodable, Sendable { case connected, needsAuth = "needs_auth", unavailable, unknown }
    let status: Status
    let needsUserInput: [String]
    let evidence: String

    enum CodingKeys: String, CodingKey { case status, needsUserInput = "needs_user_input", evidence }

    static func parse(_ data: Data) -> [String: ConnectorDetection]? {
        try? JSONDecoder().decode([String: ConnectorDetection].self, from: data)
    }

    /// Human labels for the connectors the shipped probe registry
    /// (`plugin/templates/connector-probes.yaml`) knows; unknown keys show as-is.
    static let displayNames: [String: String] = [
        "slack": "Slack", "calendar": "Google Calendar", "email": "Gmail", "linear": "Linear", "github": "GitHub",
        "granola": "Granola", "fathom": "Fathom", "drive": "Google Drive", "claude_sessions": "Claude Code sessions",
    ]
}
