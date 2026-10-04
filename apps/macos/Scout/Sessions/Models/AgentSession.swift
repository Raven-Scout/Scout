import Foundation

/// A session's derived state (Agent Sessions spec §4.7). Raw values are the
/// engine's spelling; the declaration order is the page's severity order.
nonisolated enum AgentSessionState: String, Codable, CaseIterable, Hashable, Sendable {
    case needsYou = "needs_you"
    case running
    case waiting
    case parked
    case stale
    case done

    /// 0 = most urgent. Every sort on the page goes through this.
    var severity: Int {
        switch self {
        case .needsYou: return 0
        case .running:  return 1
        case .waiting:  return 2
        case .parked:   return 3
        case .stale:    return 4
        case .done:     return 5
        }
    }

    var label: String {
        switch self {
        case .needsYou: return "Needs you"
        case .running:  return "Running"
        case .waiting:  return "Waiting"
        case .parked:   return "Parked"
        case .stale:    return "Stale"
        case .done:     return "Done"
        }
    }
}

nonisolated struct SessionWorktree: Codable, Equatable, Hashable, Sendable {
    let path: String?
    let name: String?
    let branch: String?
    let sourceBranch: String?
    /// The desktop app's `keptDirtyWorktree` flag; no live `git status` (spec §4.8).
    let dirty: Bool

    enum CodingKeys: String, CodingKey {
        case path, name, branch, dirty
        case sourceBranch = "source_branch"
    }
}

/// One linked PR. The enum-like fields stay strings: they are GitHub's values,
/// upper-case, with a missing or not-yet-computed value spelled `unknown`
/// (spec §4.6), and GitHub can add values the app has never seen.
nonisolated struct SessionPR: Codable, Equatable, Hashable, Sendable {
    let number: Int
    let repo: String
    let url: String?
    /// `OPEN` | `MERGED` | `CLOSED` | `unknown`
    let state: String
    let isDraft: Bool
    /// `""` | `APPROVED` | `CHANGES_REQUESTED` | `REVIEW_REQUIRED` | `unknown`
    let reviewDecision: String
    let reviewRequested: Bool
    /// `passing` | `failing` | `pending` | `none` | `unknown`
    let checks: String
    /// gh `mergeStateStatus`, e.g. `CLEAN` | `DIRTY` | `BLOCKED` | `unknown`
    let mergeState: String
    let fetchedAt: Date?
    /// The last fetch failed and this is the cached value.
    let stale: Bool
    let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case number, repo, url, state, checks, stale
        case isDraft = "is_draft"
        case reviewDecision = "review_decision"
        case reviewRequested = "review_requested"
        case mergeState = "merge_state"
        case fetchedAt = "fetched_at"
        case updatedAt = "updated_at"
    }

    /// Short review label for chips; nil when there is nothing to say.
    var reviewLabel: String? {
        if state == "MERGED" { return "merged" }
        if state == "CLOSED" { return "closed" }
        if isDraft { return "draft" }
        switch reviewDecision {
        case "CHANGES_REQUESTED": return "changes requested"
        case "APPROVED":          return "approved"
        case "REVIEW_REQUIRED":   return "review required"
        default:                  return reviewRequested ? "review requested" : nil
        }
    }

    /// ✓ / ✗ / … for the checks rollup; nil for `none` and `unknown`.
    var checksSymbol: String? {
        switch checks {
        case "passing": return "✓"
        case "failing": return "✗"
        case "pending": return "…"
        default:        return nil
        }
    }

    var webURL: URL? { url.flatMap(URL.init(string:)) }
}

nonisolated struct SessionLastTurn: Codable, Equatable, Hashable, Sendable {
    let at: Date?
    /// `end_turn` | `tool_use` | `question` | `unknown`
    let kind: String
}

nonisolated struct SessionTranscript: Codable, Equatable, Hashable, Sendable {
    let path: String
    let firstPrompt: String
    let filesTouched: [String]
    let toolCalls: Int
    let lastTurn: SessionLastTurn
    let mtimeNs: Int64

    enum CodingKeys: String, CodingKey {
        case path
        case firstPrompt = "first_prompt"
        case filesTouched = "files_touched"
        case toolCalls = "tool_calls"
        case lastTurn = "last_turn"
        case mtimeNs = "mtime_ns"
    }
}

/// One Claude Code session from `sessions-index.json` (spec §4.8). Keys are
/// mapped explicitly rather than with `.convertFromSnakeCase`, which would
/// also rewrite the *dictionary* keys of `counts` and `source_counts`.
nonisolated struct AgentSession: Codable, Equatable, Hashable, Identifiable, Sendable {
    let id: String
    let cliSessionID: String?
    let title: String?
    let titleSource: String?
    let projectKey: String
    let groupName: String?
    let cwd: String
    let originCwd: String
    let worktree: SessionWorktree?
    let createdAt: Date?
    let lastActivityAt: Date?
    let model: String?
    let effort: String?
    let turns: Int?
    let isArchived: Bool
    let isOpen: Bool
    let isScoutRun: Bool
    let parentSessionID: String?
    let spawnedTaskID: String?
    let scheduledTaskID: String?
    let prs: [SessionPR]
    let pr: SessionPR?
    let transcript: SessionTranscript?
    let state: AgentSessionState
    let stateReasons: [String]

    enum CodingKeys: String, CodingKey {
        case id, title, cwd, worktree, model, effort, turns, prs, pr, transcript, state
        case cliSessionID = "cli_session_id"
        case titleSource = "title_source"
        case projectKey = "project_key"
        case groupName = "group_name"
        case originCwd = "origin_cwd"
        case createdAt = "created_at"
        case lastActivityAt = "last_activity_at"
        case isArchived = "is_archived"
        case isOpen = "is_open"
        case isScoutRun = "is_scout_run"
        case parentSessionID = "parent_session_id"
        case spawnedTaskID = "spawned_task_id"
        case scheduledTaskID = "scheduled_task_id"
        case stateReasons = "state_reasons"
    }

    /// The desktop title, else the first line of the first prompt (CLI-only
    /// sessions have no title), else a placeholder.
    var displayTitle: String {
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return title }
        let firstLine = transcript?.firstPrompt
            .split(whereSeparator: \.isNewline).first
            .map { String($0).trimmingCharacters(in: .whitespaces) } ?? ""
        if firstLine.isEmpty { return "(untitled)" }
        return firstLine.count > 120 ? String(firstLine.prefix(120)) + "…" : firstLine
    }

    /// The reason shown on a card: the deciding one (the engine lists it first).
    var primaryReason: String? { stateReasons.first }
}
