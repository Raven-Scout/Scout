import Foundation

/// Small pure formatters the Sessions views share.
nonisolated enum SessionsFormat {

    /// `40s ago`, `12m ago`, `2h ago`, `3d ago` — the engine's own spelling
    /// (`derive.fmt_ago`), so cards and reasons read the same.
    static func ago(_ date: Date?, now: Date) -> String {
        guard let date else { return "—" }
        let seconds = max(0, Int(now.timeIntervalSince(date)))
        if seconds < 60 { return "\(seconds)s ago" }
        if seconds < 3600 { return "\(seconds / 60)m ago" }
        if seconds < 86_400 { return "\(seconds / 3600)h ago" }
        return "\(seconds / 86_400)d ago"
    }

    /// `#98 · changes requested · ✗`
    static func prChip(_ pr: SessionPR) -> String {
        ["#\(pr.number)", pr.reviewLabel, pr.checksSymbol].compactMap { $0 }.joined(separator: " · ")
    }

    /// The header's freshness line: when the index was last rebuilt and how the
    /// last PR check went. A single PR problem is named — it is usually the one
    /// thing to fix, such as `gh` missing from PATH; several are counted.
    static func freshness(lastRefreshAt: Date?, prFinishedAt: Date?, prErrors: [String], now: Date) -> String {
        let parts = [updatedPart(lastRefreshAt: lastRefreshAt, now: now),
                     prCheckPart(prFinishedAt: prFinishedAt, prErrors: prErrors, now: now)].compactMap { $0 }
        return parts.isEmpty ? "Reading your Claude Code sessions…" : parts.joined(separator: " · ")
    }

    /// `updated 10s ago`, the freshness line's first half.
    static func updatedPart(lastRefreshAt: Date?, now: Date) -> String? {
        lastRefreshAt.map { "updated \(ago($0, now: now))" }
    }

    /// The freshness line's PR half. The header makes it a button that lists
    /// the problems whenever there is at least one (UI audit R4).
    static func prCheckPart(prFinishedAt: Date?, prErrors: [String], now: Date) -> String? {
        guard let prFinishedAt else { return nil }
        switch prErrors.count {
        case 0:  return "PRs checked \(ago(prFinishedAt, now: now))"
        case 1:  return "PR check failed: \(prErrors[0])"
        default: return "PR check: \(prErrors.count) problems"
        }
    }

    /// The PR-problems popover's opening sentence.
    static func prProblemsSummary(count: Int) -> String {
        count == 1
            ? "Scout couldn't check a pull request on GitHub, so its state on the board may be out of date."
            : "Scout couldn't check \(count) pull requests on GitHub, so their states on the board may be out of date."
    }

    /// `claude-opus-5` → `opus-5`
    static func shortModel(_ model: String) -> String {
        model.hasPrefix("claude-") ? String(model.dropFirst("claude-".count)) : model
    }

    /// Where "Resume" runs `claude --resume`. `claude --resume <id>` finds a
    /// session only from the folder it ran in, so that folder comes first:
    /// the `cwd` its transcript records (the index's transcript path, else a
    /// search of `claudeHome/projects` by id, as Action Items does), then the
    /// index's `cwd`. When that folder is gone, the project folder (spec
    /// §6.4), flagged so the page can say so. nil when neither exists: the
    /// page says so rather than resuming from the home folder, where the
    /// session is never found (UI audit #32). Reads files, so callers run it
    /// off the main thread. `claudeHome` nil skips the search (tests).
    static func resumeFolder(
        for session: AgentSession,
        claudeHome: URL?,
        exists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) }
    ) -> ResumeFolder? {
        if let path = session.transcript?.path, !path.isEmpty,
           let folder = ClaudeSessionFolder.cwd(inTranscript: URL(fileURLWithPath: path)) {
            return .own(folder)
        }
        if let id = session.cliSessionID, let claudeHome,
           let folder = ClaudeSessionFolder.folder(forSessionID: id, claudeHome: claudeHome) {
            return .own(folder)
        }
        if !session.cwd.isEmpty, exists(session.cwd) {
            return .own(URL(fileURLWithPath: session.cwd, isDirectory: true))
        }
        if !session.originCwd.isEmpty, session.originCwd != session.cwd, exists(session.originCwd) {
            return .project(URL(fileURLWithPath: session.originCwd, isDirectory: true))
        }
        return nil
    }
}

/// The folder "Resume" runs in, and whether it is the session's own.
nonisolated enum ResumeFolder: Equatable, Sendable {
    /// The folder the session ran in, where `claude --resume` finds it.
    case own(URL)
    /// The session's own folder is gone (a removed worktree); this is its
    /// project folder, where `claude --resume` may not find it.
    case project(URL)

    var url: URL {
        switch self {
        case .own(let url), .project(let url): return url
        }
    }
}
