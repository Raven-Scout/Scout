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

    /// `claude-opus-5` → `opus-5`
    static func shortModel(_ model: String) -> String {
        model.hasPrefix("claude-") ? String(model.dropFirst("claude-".count)) : model
    }

    /// Where "Resume in terminal" runs `claude --resume`: the session's own
    /// folder, else its project folder when the worktree is gone (spec §6.4),
    /// else the home folder.
    static func resumeDirectory(
        for session: AgentSession,
        exists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) }
    ) -> URL {
        for path in [session.cwd, session.originCwd] where !path.isEmpty && exists(path) {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser
    }
}
