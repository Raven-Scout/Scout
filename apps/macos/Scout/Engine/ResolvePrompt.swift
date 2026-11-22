import Foundation

/// What "Resolve with Claude" can investigate: a run-health finding, a connector alert, or one
/// run. The engine builds the prompt for it (`scoutctl health resolve-prompt <kind> <key>`);
/// the app never writes prompt text itself.
nonisolated enum ResolveSubject: Equatable, Hashable, Sendable {
    /// A finding id, such as `run.integrity_restored` or `slot.stale:morning-briefing`.
    case finding(String)
    /// A `connector_key` exactly as `connector-alerts.json` holds it, such as `mcp:example_tracker`.
    case connector(String)
    /// A run log's file name under `.scout-logs/`, never a path.
    case run(logName: String)

    var kind: String {
        switch self {
        case .finding: return "finding"
        case .connector: return "connector"
        case .run: return "run"
        }
    }

    var key: String {
        switch self {
        case .finding(let id): return id
        case .connector(let key): return key
        case .run(let name): return name
        }
    }

    /// The engine's patterns (`scout/resolve_prompt.py` `KEY_PATTERNS`), matched whole.
    private static let patterns: [String: String] = [
        "finding": #"^[a-z][a-z0-9_.]{0,63}(:[A-Za-z0-9][A-Za-z0-9._-]{0,80})?$"#,
        "connector": #"^[a-z][a-z0-9_-]{0,31}(:[A-Za-z0-9_.-]{1,80})?$"#,
        "run": #"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.log$"#,
    ]

    /// Whether `key` is one the engine accepts; checked before anything runs.
    var isValidKey: Bool {
        guard let pattern = Self.patterns[kind] else { return false }
        return key.range(of: pattern, options: .regularExpression) != nil
    }
}

/// One `scoutctl health resolve-prompt … --json` document (`schema_version` 1). Contract:
/// `plugin/engine/tests/fixtures/contract/resolve-prompt.json`.
nonisolated struct ResolvePrompt: Equatable, Sendable {
    let kind: String
    let key: String
    /// Whether the engine still sees the problem; the prompt says what to do either way.
    let current: Bool
    let title: String
    /// The vault the prompt is for. The app refuses one that isn't its own vault.
    let cwd: String
    let prompt: String

    static let schemaVersion = 1

    private struct Wire: Decodable {
        let schema_version: Int
        let kind: String
        let key: String
        let current: Bool
        let title: String
        let cwd: String
        let prompt: String
    }

    /// nil unless `schema_version` is 1, every key is present, and `kind` and `key` are `subject`'s.
    static func parse(_ data: Data, for subject: ResolveSubject) -> ResolvePrompt? {
        guard let w = try? JSONDecoder().decode(Wire.self, from: data),
              w.schema_version == schemaVersion,
              w.kind == subject.kind, w.key == subject.key,
              !w.prompt.isEmpty
        else { return nil }
        return ResolvePrompt(kind: w.kind, key: w.key, current: w.current, title: w.title, cwd: w.cwd, prompt: w.prompt)
    }
}
