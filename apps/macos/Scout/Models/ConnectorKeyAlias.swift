import Foundation

/// Normalizes historical / renamed connector keys to their canonical form so
/// the matrix, alert banner, and label map only ever see one identity per
/// connector.
///
/// The 14-day rolling window means a key rename on the plugin side leaves
/// legacy-keyed call rows in `.scout-logs/connector-calls-*.jsonl` for up to
/// two weeks. Without aliasing they orphan into rows that no roster entry
/// matches — silently dropping data. The canonical key comes from
/// `connectors.snapshot.json`; legacy keys map onto whichever canonical key
/// represents the same underlying connector today.
///
/// Add a row here whenever a connector is renamed plugin-side. Tests in
/// `ConnectorKeyAliasTests` lock the mapping.
enum ConnectorKeyAlias {
    /// Legacy → canonical. Only includes keys that have actually been renamed
    /// in the wild; unknown keys pass through untouched (heuristic prettifier
    /// in the rail card handles brand-new connectors).
    static let legacyAliases: [String: String] = [
        "mcp:plugin_slack_slack":   "mcp:claude_ai_Slack",
        "mcp:plugin_linear_linear": "mcp:claude_ai_Linear"
    ]

    /// Canonicalize a connector key. Pass-through when no alias is known.
    static func canonical(_ key: String) -> String {
        legacyAliases[key] ?? key
    }
}
