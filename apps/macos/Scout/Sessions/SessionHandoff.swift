import Foundation

/// The "Copy handoff" text (spec §6.4): what someone — or another agent —
/// needs to pick the session up, as Markdown.
nonisolated enum SessionHandoff {
    static func markdown(for session: AgentSession, projectName: String) -> String {
        var lines = ["## \(session.displayTitle)", ""]
        lines.append("- **Project:** \(projectName)")
        var state = "- **State:** \(session.state.label.lowercased())"
        if !session.stateReasons.isEmpty { state += " — " + session.stateReasons.joined(separator: "; ") }
        lines.append(state)
        if let pr = session.pr {
            var line = "- **PR:** \(pr.repo)#\(pr.number)"
            if let label = pr.reviewLabel { line += " (\(label))" }
            if let url = pr.url { line += " — \(url)" }
            lines.append(line)
        }
        if let branch = session.worktree?.branch {
            var line = "- **Branch:** `\(branch)`"
            if let name = session.worktree?.name { line += " in worktree `\(name)`" }
            lines.append(line)
        }
        lines.append("- **Folder:** `\(session.cwd)`")
        if let id = session.cliSessionID {
            lines.append("- **Resume:** `claude --resume \(id)`")
        }
        if let prompt = session.transcript?.firstPrompt, !prompt.isEmpty {
            lines += ["", "### First prompt", ""]
            lines += prompt.split(separator: "\n", omittingEmptySubsequences: false).map { "> \($0)" }
        }
        if let files = session.transcript?.filesTouched, !files.isEmpty {
            lines += ["", "### Files touched", ""]
            lines += files.map { "- `\($0)`" }
        }
        return lines.joined(separator: "\n")
    }
}
