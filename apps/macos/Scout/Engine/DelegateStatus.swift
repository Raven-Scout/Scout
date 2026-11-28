import Foundation

/// `scoutctl delegate status --json` (engine `scout/delegate_status.py`, #550): each delegate
/// mirror ("agent copy") — a filtered copy of the vault one of the owner's own agents reads off
/// this Mac — with its remote, last sync, why a sync was refused, and what the copy held.
///
/// A contract with the engine: `ScoutTests/Fixtures/delegate-status.json` is a byte copy of the
/// engine's generated `plugin/engine/tests/fixtures/contract/delegate-status.json`. The engine
/// shape-checks the state it reads and never puts a note's content or name, a vault path, a key
/// or a token in it; this app never reads the state file, the keys or the push token itself.
/// Status values stay strings, so a value this build doesn't know never fails the decode.
nonisolated struct DelegateStatus: Equatable, Sendable, Decodable {
    nonisolated struct Remote: Equatable, Sendable, Decodable {
        /// github | local | other
        let kind: String
        /// `owner/name` for GitHub, a path (home as `~`) for a local mirror; nil for other.
        let display: String?
    }

    nonisolated struct Refusal: Equatable, Sendable, Decodable {
        /// The engine's refusal code, e.g. `mirror_diverged`; `unknown` when the state held something odd.
        let code: String
        /// A fixed sentence for the code (never the refusal's own message, which can name vault paths).
        let reason: String
        let at: String?
        /// Failing since (the first failure of the current streak).
        let since: String?
    }

    nonisolated struct Counts: Equatable, Sendable, Decodable {
        let files: Int
        let withheld: Int
        let leftOut: Int
        let redactedLines: Int
        /// Files left out because agents read them as instructions or settings (#693).
        let agentConfig: Int

        private enum CodingKeys: String, CodingKey {
            case files, withheld, leftOut = "left_out", redactedLines = "redacted_lines", agentConfig = "agent_config"
        }
    }

    /// The agent's inbox (V4b3): ids, counts and times from the signed inbox state, never the agent's text.
    nonisolated struct Inbox: Equatable, Sendable, Decodable {
        nonisolated struct Refusal: Equatable, Sendable, Decodable {
            let code: String
            /// A fixed sentence for the code.
            let reason: String
            let at: String?
        }

        let linkedAt: String?
        let lastPull: String?
        let refusal: Refusal?
        /// Pulled items that wait for the owner: observations to accept and held comments.
        let review: Int?
        let oldestReview: String?
        /// Taken, not settled yet (for the owner, or a retry).
        let waiting: Int?
        /// Files in the inbox folder no pull took: something else put them there.
        let untaken: Int?

        private enum CodingKeys: String, CodingKey {
            case refusal, review, waiting, untaken
            case linkedAt = "linked_at", lastPull = "last_pull", oldestReview = "oldest_review"
        }
    }

    nonisolated struct Delegate: Equatable, Sendable, Decodable, Identifiable {
        let id: String
        /// Has a mirror on this Mac (`scoutctl delegate link`).
        let linked: Bool
        /// Listed under `privacy.delegates` in scout-config.yaml.
        let listed: Bool
        /// The link state's signature didn't match: shown for the owner to check, trusted for nothing.
        let stateUnverified: Bool
        /// nil unless the owner is at the keyboard (and for an unlinked delegate).
        let remote: Remote?
        let linkedAt: String?
        let lastSync: String?
        let lastAttempt: String?
        /// ok | failed | pending | never
        let lastResult: String
        let refusal: Refusal?
        /// The vault moved since the last good sync (nil: not known).
        let behind: Bool?
        let deployKey: Bool?
        /// Old deploy keys GitHub didn't delete yet.
        let oldKeysLive: Int?
        let counts: Counts?
        let agentConfigLeftOut: Int?
        /// The CLI's `delegate sync <id>` may run for it now (owner at the keyboard, trusted state, linked).
        let canSync: Bool
        /// The CLI's `delegate preview <id>` may run for it now (owner at the keyboard, listed).
        let canPreview: Bool
        /// Its inbox, or nil (none linked, an engine before V4b3, or an inbox state that can't be trusted).
        let inbox: Inbox?
        /// The CLI's inbox review may run for it now (owner at the keyboard, a trusted inbox); nil before V4b3.
        let canReview: Bool?

        private enum CodingKeys: String, CodingKey {
            case id, linked, listed, remote, refusal, behind, counts
            case stateUnverified = "state_unverified", linkedAt = "linked_at", lastSync = "last_sync"
            case lastAttempt = "last_attempt", lastResult = "last_result", deployKey = "deploy_key"
            case oldKeysLive = "old_keys_live", agentConfigLeftOut = "agent_config_left_out"
            case canSync = "can_sync", canPreview = "can_preview", inbox, canReview = "can_review"
        }
    }

    let schemaVersion: Int
    /// ok | none | unverified | unreadable — the link state file as a whole.
    let state: String
    /// Whether the engine saw the owner at the keyboard (remotes and actions are only for the owner).
    let owner: Bool
    /// Entries in the state the engine dropped because their id was malformed.
    let skipped: Int
    let delegates: [Delegate]
    /// ok | none | unverified | unsafe | unreadable — the inbox state as a whole; nil before V4b3.
    var inboxState: String? = nil

    private enum CodingKeys: String, CodingKey {
        case state, owner, skipped, delegates, schemaVersion = "schema_version", inboxState = "inbox_state"
    }

    /// The schema this build reads; a newer one is refused rather than half-read.
    static let supportedSchema = 1

    static func parse(_ data: Data) -> DelegateStatus? {
        guard let status = try? JSONDecoder().decode(DelegateStatus.self, from: data),
              status.schemaVersion == supportedSchema else { return nil }
        return status
    }

    /// A delegate id as the engine accepts one (`is_delegate_id`, matched whole: no trailing newline, and not the
    /// reserved `author`, which `--to` reads as the note's author). The app passes only these to scoutctl.
    static func isDelegateID(_ id: String) -> Bool { id != "author" && wholeMatch(id, #"[a-z0-9][a-z0-9-]{0,39}"#) }

    /// A refusal code as the engine writes one, matched whole.
    static func isRefusalCode(_ code: String) -> Bool { wholeMatch(code, #"[a-z][a-z_]{0,47}"#) }

    /// Whether all of `text` matches `pattern`. Unlike `^…$` with `.regularExpression`, a trailing newline fails.
    private static func wholeMatch(_ text: String, _ pattern: String) -> Bool {
        guard let regex = try? Regex(pattern) else { return false }
        return (try? regex.wholeMatch(in: text)) != nil
    }
}

/// The counts of `scoutctl delegate preview <id> --json` or `delegate sync <id> --json`. Only the
/// numbers are read: the rest of that output (notes, left-out file names) can name vault paths,
/// so the app never decodes or shows it.
nonisolated struct DelegateMirrorCounts: Equatable, Sendable, Decodable {
    let files: Int
    let withheld: Int
    let leftOut: Int
    let redactedLines: Int
    /// Absent from engines before the Agent copies card.
    let agentConfig: Int?
    /// Sync only: the mirror already matched, nothing was pushed.
    let unchanged: Bool?

    private enum CodingKeys: String, CodingKey {
        case files, withheld, unchanged
        case leftOut = "left_out", redactedLines = "redacted_lines", agentConfig = "agent_config"
    }

    static func parse(_ data: Data) -> DelegateMirrorCounts? {
        try? JSONDecoder().decode(DelegateMirrorCounts.self, from: data)
    }
}
