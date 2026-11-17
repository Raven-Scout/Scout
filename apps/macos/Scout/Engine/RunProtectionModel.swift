import Foundation

/// Plain-language presentation of `RunProtectionStatus` for the Run protection card in
/// Settings ▸ Engine (app UI audit, card C9). Views stay thin; this is what the tests pin.
/// Owner-facing words: "write protection" (never "sandbox" or "seatbelt"), "privacy checks"
/// (never "gates" or "probe"), "file check" for the post-run integrity check.
nonisolated struct RunProtectionModel: Equatable, Sendable {
    nonisolated enum Tone: Equatable, Sendable { case ok, warn, bad, neutral }

    /// One labelled line of the card.
    nonisolated struct Line: Equatable, Sendable, Identifiable {
        let id: String
        let title: String
        let value: String
        let help: String
        let tone: Tone
    }

    /// What a problem's button does.
    nonisolated enum Action: Equatable, Sendable {
        case turnOnProtection, checkProtectionAgain, checkPrivacyAgain, showLog(URL)

        var buttonTitle: String {
            switch self {
            case .turnOnProtection: return "Turn on"
            case .checkProtectionAgain, .checkPrivacyAgain: return "Check again"
            case .showLog: return "Show log"
            }
        }
    }

    nonisolated struct ProblemRow: Equatable, Sendable, Identifiable {
        let id: String
        let summary: String
        let fix: String
        let tone: Tone
        let action: Action?
    }

    let status: RunProtectionStatus
    /// The vault the engine reported on: `log` paths are relative to it.
    let vault: URL?
    let now: Date

    init(status: RunProtectionStatus, vault: URL?, now: Date = Date()) {
        self.status = status; self.vault = vault; self.now = now
    }

    // MARK: - Headline

    var headline: String {
        if pausedByPrivacyChecks { return "Scheduled runs are paused" }
        if status.severity == "green" && status.sandbox.status == "not_checked" { return "Write protection not tested yet" }
        switch status.severity {
        case "green": return "Scheduled runs are protected"
        case "red": return "Run protection needs attention"
        default: return "Scheduled runs are partly protected"
        }
    }
    var headlineTone: Tone {
        if status.severity == "green" && status.sandbox.status == "not_checked" { return .neutral }
        return Self.tone(severity: status.severity)
    }

    private var pausedByPrivacyChecks: Bool {
        status.problems.contains { ["gates.probe_failed", "gates.not_loaded", "gates.probe_unsafe"].contains($0.id) }
    }

    // MARK: - Lines

    var lines: [Line] { [writeProtection, privacyChecks, fileCheck, broker] }

    var writeProtection: Line {
        let s = status.sandbox
        let seen: String = {
            switch s.checkedBy {
            case "last_run": return " The last scheduled run says so; Check now tests it again."
            case "self_test": return " Tested just now."
            default: return ""
            }
        }()
        let (value, help, tone): (String, String, Tone) = {
            switch s.status {
            case "enforced": return ("On", "Scheduled runs can't change the files that decide which of Scout's checks run.\(seen)", .ok)
            case "not_checked": return ("On, not tested yet", "No scheduled run has used it since it was turned on. Check now tests it without starting a run.", .neutral)
            case "off": return ("Off", "You turned it off. Runs can change Scout's own scripts and settings; the privacy checks still run.", .warn)
            case "failed": return ("Couldn't start", "Expected on this Mac but couldn't be set up, so scheduled runs are skipped until it can.\(seen)", .bad)
            case "unavailable": return ("Not available here", "This system has no write protection for Scout yet; runs go ahead without it.", .warn)
            case "conflict": return ("Claude Code's sandbox instead", "Claude Code's own sandbox is on in your Claude settings, and the two can't run together.", .warn)
            default: return ("Unknown", "Scout couldn't tell whether runs get write protection.", .neutral)
            }
        }()
        return Line(id: "write", title: "Write protection", value: value, help: help, tone: tone)
    }

    var privacyChecks: Line {
        let g = status.gateProbe
        let when = g.checkedAt.flatMap(Self.date).map { " Checked \(relative($0))." } ?? ""
        let (value, help, tone): (String, String, Tone) = {
            switch g.status {
            case "passed":
                let version = g.claudeVersion.map { "Claude Code \($0)" } ?? "this Claude Code"
                let stale = g.stale ? " The next run checks again." : ""
                return ("Verified", "Scout's privacy checks load in \(version).\(when)\(stale)", .ok)
            case "failed":
                return ("Didn't load", "Scheduled runs are paused: this Claude Code didn't load Scout's privacy checks.\(when)", .bad)
            case "unsafe":
                return ("Couldn't check safely", "Scheduled runs are paused until Scout can check its privacy checks on this Mac.\(when)", .bad)
            case "not_checked":
                return ("Not checked yet", "Scout checks before each scheduled run.", .neutral)
            default:
                return ("Unknown", g.reason, .neutral)
            }
        }()
        return Line(id: "privacy", title: "Privacy checks", value: value, help: help, tone: tone)
    }

    var fileCheck: Line {
        guard let run = status.lastRun else {
            return Line(id: "files", title: "Last run's file check", value: "No runs yet",
                        help: "After each run Scout checks that it didn't change its own scripts and settings.", tone: .neutral)
        }
        let which = "\(Self.slotTitle(run.slot)), \(Self.date(run.finishedAt).map(relative) ?? run.finishedAt)"
        let (value, extra, tone): (String, String, Tone) = {
            switch run.integrity {
            case "clean": return ("Clean", "", .ok)
            case "restored": return ("Files put back", " It changed a protected file; Scout put it back in a new commit.", .bad)
            case "unrestored": return ("Change still held", " It changed a protected file; a tag or the stash still holds the change.", .bad)
            case "error": return ("Couldn't run", " The check couldn't run after this run.", .warn)
            default: return ("Not checked", " This run wasn't checked.", .neutral)
            }
        }()
        return Line(id: "files", title: "Last run's file check", value: value, help: which + "." + extra, tone: tone)
    }

    var broker: Line {
        let b = status.broker
        let (value, help, tone): (String, String, Tone) = {
            switch b.status {
            case "on": return ("On", "Runs commit and send Telegram messages through Scout, never directly.", .ok)
            case "unavailable": return ("Didn't start", "The last run could write git history and read the Telegram token directly.", .warn)
            case "off": return ("Not used", "Used only by runs with write protection on.", .neutral)
            case "not_recorded": return ("Not recorded yet", "Scout records it with each protected run.", .neutral)
            default: return ("Unknown", b.reason, .neutral)
            }
        }()
        return Line(id: "broker", title: "Commit and message broker", value: value, help: help, tone: tone)
    }

    // MARK: - Slots

    /// One line per scheduled slot: which agent runs it, which model, and its limits.
    var slotLines: [Line] {
        status.slots.map { slot in
            let title = Self.slotTitle(slot.key)
            if let error = slot.error {
                return Line(id: "slot-\(slot.key)", title: title, value: "Can't run", help: error, tone: .bad)
            }
            let runtime = Self.runtimeName(slot.runtime)
            let value = [runtime, slot.model].compactMap { $0 }.joined(separator: " · ")
            var help = [Self.timeLimit(slot)]
            if let tokens = Self.tokenLimit(slot) { help.append(tokens) }
            if !slot.ignored.isEmpty { help.append("Ignored (would loosen a limit): \(slot.ignored.joined(separator: ", "))") }
            return Line(id: "slot-\(slot.key)", title: title, value: value, help: help.joined(separator: ". ") + ".", tone: .neutral)
        }
    }

    static func timeLimit(_ slot: RunProtectionStatus.Slot) -> String {
        guard let seconds = slot.runTimeoutS else {
            return slot.timeoutSource == "owner" ? "No time limit (you turned it off)" : "No time limit"
        }
        let minutes = seconds / 60
        let amount = minutes % 60 == 0 && minutes >= 60 ? "\(minutes / 60) h" : "\(minutes) min"
        let by: String = {
            switch slot.timeoutSource {
            case "owner": return ", set by you"
            case "config": return ", set in scout-config.yaml"
            default: return ""
            }
        }()
        return "Stops after \(amount) awake\(by)"
    }

    static func tokenLimit(_ slot: RunProtectionStatus.Slot) -> String? {
        guard let tokens = slot.maxTokensPerDay else { return nil }
        let amount = tokens >= 1_000_000 ? "\(tokens / 1_000_000)M" : "\(tokens / 1000)K"
        return "At most \(amount) tokens a day"
    }

    static func runtimeName(_ id: String?) -> String? {
        switch id {
        case nil: return nil
        case "claude-code": return "Claude Code"
        case let other?: return other
        }
    }

    /// `morning-briefing` → "Morning briefing".
    static func slotTitle(_ key: String) -> String {
        let words = key.replacingOccurrences(of: "-", with: " ").replacingOccurrences(of: "_", with: " ")
        return words.prefix(1).uppercased() + words.dropFirst()
    }

    // MARK: - Problems

    var problems: [ProblemRow] {
        status.problems.map { p in
            ProblemRow(id: p.id, summary: p.summary, fix: p.fix, tone: Self.tone(severity: p.severity), action: action(for: p))
        }
    }

    func action(for problem: RunProtectionStatus.Problem) -> Action? {
        switch problem.action {
        case "turn_on_sandbox": return .turnOnProtection
        case "check_sandbox": return .checkProtectionAgain
        case "check_gates": return .checkPrivacyAgain
        case "show_log":
            // The engine shape-checks it; refuse anything that would leave the vault anyway.
            guard let rel = problem.log, let vault, !rel.hasPrefix("/"),
                  !rel.split(separator: "/").contains("..") else { return nil }
            return .showLog(vault.appendingPathComponent(rel))
        default: return nil
        }
    }

    // MARK: - The owner's switch

    /// Offered when protection is on, or would be but couldn't start: "Turn off…", behind the confirmation.
    var offersTurnOff: Bool {
        status.sandbox.switch == "on" && ["enforced", "failed", "not_checked"].contains(status.sandbox.status)
    }
    /// Offered when the owner turned it off.
    var offersTurnOn: Bool { status.sandbox.switch == "off" }

    static let turnOffTitle = "Turn off write protection?"
    static let turnOffMessage = """
        Scheduled runs will be able to change Scout's own runner scripts, agent settings and schedule, which \
        decide whether Scout's privacy checks run at all. A run that went wrong, or a page it read that told it \
        to, could switch those checks off for every later run.

        Scout's privacy checks still run, and the file check after each run still puts protected files back. \
        Turn it off only to work around a problem, and turn it back on afterwards.
        """
    static let turnOffConfirm = "Turn Off"

    // MARK: -

    static func tone(severity: String) -> Tone {
        switch severity {
        case "green": return .ok
        case "red": return .bad
        case "yellow": return .warn
        default: return .neutral
        }
    }

    static func date(_ iso: String) -> Date? { ISO8601DateFormatter().date(from: iso) }

    private func relative(_ date: Date) -> String {
        RelativeDateTimeFormatter().localizedString(for: date, relativeTo: now)
    }
}
