import Foundation

/// Plain-language presentation of `DelegateStatus` for Settings ▸ Agent copies (#550). Views stay
/// thin; this is what the tests pin. Owner-facing words: "agent copy" for a delegate mirror,
/// "your agent" for the delegate. Commands the card can't run are named for Terminal.
nonisolated struct AgentCopiesModel: Equatable, Sendable {
    nonisolated enum Tone: Equatable, Sendable { case ok, warn, bad, neutral }

    nonisolated struct Row: Equatable, Sendable, Identifiable {
        let id: String
        let value: String
        let help: String
        let tone: Tone
        let offersSync: Bool
        let offersPreview: Bool
    }

    let status: DelegateStatus
    let results: [String: DelegateStatusService.Outcome]
    let now: Date

    init(status: DelegateStatus, results: [String: DelegateStatusService.Outcome] = [:], now: Date = Date()) {
        self.status = status; self.results = results; self.now = now
    }

    /// Whether Settings shows the card: only once there is something to show (most vaults link no agent).
    var isVisible: Bool { status.state != "none" || !status.delegates.isEmpty }

    static let intro = "Filtered copies of your vault that your own agents off this Mac read. "
        + "Linking, unlinking and deploy keys stay in Terminal (scoutctl delegate)."

    /// A problem with the link state as a whole, or nil.
    var stateProblem: (summary: String, fix: String)? {
        switch status.state {
        case "unverified":
            return ("The agent-copies state wasn't signed by Scout on this Mac, so no copy syncs until you check it.",
                    "Check each link and sign it again in Terminal: scoutctl delegate trust-state")
        case "unreadable":
            return ("The agent-copies state can't be read, so no copy syncs.",
                    "Fix or delete .scout-state/delegate-mirrors.json in the vault, then link each agent again "
                        + "in Terminal: scoutctl delegate link")
        default:
            return nil
        }
    }

    var rows: [Row] { status.delegates.map(row) }

    func row(_ d: DelegateStatus.Delegate) -> Row {
        var help: [String] = []
        let (value, tone) = headline(d)
        if let remote = d.remote, let text = Self.remoteText(remote) { help.append(text) }
        if let refusal = d.refusal {
            let since = refusal.since.flatMap(Self.date).map { " Failing since \(relative($0))." } ?? ""
            help.append(refusal.reason + since)
        }
        if let counts = d.counts { help.append(Self.countsText(counts, prefix: "Holds")) }
        if let n = d.agentConfigLeftOut, n > 0 {
            help.append("\(Self.files(n)) left out as agent config (instructions or settings agents would read).")
        }
        if let n = d.oldKeysLive, n > 0 {
            help.append("\(n) old deploy key\(n == 1 ? " is" : "s are") still live; "
                        + "scoutctl delegate key \(d.id) retries removing \(n == 1 ? "it" : "them").")
        }
        if !d.linked {
            help.append("Listed in scout-config.yaml, not linked yet. Link it in Terminal: scoutctl delegate link \(d.id)")
        } else if !d.listed {
            help.append("No longer listed under privacy.delegates in scout-config.yaml, so it can't sync.")
        }
        if d.stateUnverified { help.append("Not trusted until you run scoutctl delegate trust-state in Terminal.") }
        if let outcome = results[d.id] { help.append(Self.outcomeText(outcome)) }
        return Row(id: d.id, value: value, help: help.joined(separator: " "), tone: tone,
                   offersSync: d.canSync && DelegateStatus.isDelegateID(d.id),
                   offersPreview: d.canPreview && DelegateStatus.isDelegateID(d.id))
    }

    private func headline(_ d: DelegateStatus.Delegate) -> (String, Tone) {
        if d.stateUnverified { return ("Not trusted", .warn) }
        if !d.linked { return ("Not linked", .neutral) }
        switch d.lastResult {
        case "failed": return ("Sync refused", .bad)
        case "pending": return ("Last push unconfirmed", .warn)
        case "never": return ("Never synced", .neutral)
        case "ok":
            let when = d.lastSync.flatMap(Self.date).map { "Synced \(relative($0))" } ?? "Synced"
            return d.behind == true ? ("\(when), update due", .neutral) : (when, .ok)
        default: return ("Unknown", .neutral)
        }
    }

    static func remoteText(_ remote: DelegateStatus.Remote) -> String? {
        guard let display = remote.display else { return nil }
        switch remote.kind {
        case "github": return "GitHub: \(display)."
        case "local": return "Folder: \(display)."
        default: return nil
        }
    }

    static func countsText(_ c: DelegateStatus.Counts, prefix: String) -> String {
        counts(files: c.files, withheld: c.withheld, leftOut: c.leftOut, lines: c.redactedLines, prefix: prefix)
    }

    static func outcomeText(_ outcome: DelegateStatusService.Outcome) -> String {
        switch outcome {
        case .preview(let c):
            var text = counts(files: c.files, withheld: c.withheld, leftOut: c.leftOut, lines: c.redactedLines,
                              prefix: "Preview: would hold")
            if let n = c.agentConfig, n > 0 { text += " \(files(n)) left out as agent config." }
            return text
        case .synced(let c):
            return c.unchanged == true ? "Synced just now: already up to date." : "Synced just now: pushed a new copy."
        case .refused(.preview, let code):
            return "The preview just now was refused (\(code))."
        case .refused(_, let code):
            return "The sync just now was refused (\(code))."
        }
    }

    private static func counts(files n: Int, withheld: Int, leftOut: Int, lines: Int, prefix: String) -> String {
        var parts = ["\(withheld) withheld"]
        if leftOut > 0 { parts.append("\(leftOut) left out") }
        if lines > 0 { parts.append("\(lines) line\(lines == 1 ? "" : "s") removed") }
        return "\(prefix) \(files(n)); \(parts.joined(separator: ", "))."
    }

    private static func files(_ n: Int) -> String { "\(n) file\(n == 1 ? "" : "s")" }

    static func date(_ iso: String) -> Date? { ISO8601DateFormatter().date(from: iso) }

    private func relative(_ date: Date) -> String {
        RelativeDateTimeFormatter().localizedString(for: date, relativeTo: now)
    }
}
