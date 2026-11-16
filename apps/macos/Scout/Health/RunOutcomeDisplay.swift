import Foundation

/// Plain-language wording for everything the run ledger and run health name:
/// outcomes, failure classes, write protection (the sandbox), the integrity
/// check, and finding severities. The one place for it, so the plain-language
/// pass (card C4, `Utilities/PlainLanguage/DisplayName.swift` on its branch) can
/// absorb it: the outcome and failure-class strings below are C4's, verbatim.
/// Engine words ("sandbox", "seatbelt", "gates", "lease") never reach the owner.
nonisolated enum RunOutcomeDisplay {

    // MARK: Outcomes

    /// "Finished", "Skipped: privacy checks didn't load", … for a ledger
    /// `outcome` (+ `failure_class`, which tells a timeout from a failure).
    static func outcome(_ outcome: String, failureClass: String? = nil) -> String {
        if outcome == "failed", let failureClass, let special = statuses[normalized(failureClass)] { return special }
        return statuses[normalized(outcome)] ?? statuses["failed"]!
    }

    /// Same table, keyed by the app's `RunStatus`.
    static func status(_ status: RunStatus) -> String {
        statuses[normalized(status.rawValue)] ?? humanize(status.rawValue)
    }

    /// One sentence on why a run ended that way; nil when nothing needs saying.
    static func reason(outcome: String, failureClass: String?) -> String? {
        if outcome.hasPrefix("skipped_") {
            if let failureClass, let why = failureClasses[normalized(failureClass)]?.why { return why }
            return statusReasons[normalized(outcome)]
        }
        guard outcome != "ok" else { return nil }
        return explanation(failureClass: failureClass ?? "unknown").why
    }

    /// Title, why and what to do for a `failure_class`.
    static func explanation(failureClass: String) -> PlainReason {
        failureClasses[normalized(failureClass)]
            ?? PlainReason(title: humanize(failureClass), why: "The run stopped with an error.",
                           fix: "Open the run's log for details.")
    }

    // MARK: Write protection and the integrity check

    /// nil when there is nothing to say (no value from an older runner).
    static func sandbox(_ raw: String?, source: String? = nil) -> String? {
        switch raw {
        case "enforced": return "Write protection on"
        case "off":
            switch source {
            case "switch": return "Write protection turned off in Settings"
            case "env": return "Write protection turned off for this Mac's runs"
            default: return "Write protection off"
            }
        case "unavailable": return "Write protection isn't available on this Mac"
        case "conflict": return "Write protection clashed with another setting"
        case "failed": return "Write protection couldn't start"
        case nil: return nil
        case let other?: return "Write protection: \(humanize(other))"
        }
    }

    static func sandboxIsConcern(_ raw: String?) -> Bool {
        guard let raw else { return false }
        return raw != "enforced"
    }

    static func integrity(_ raw: String?) -> String? {
        switch raw {
        case "clean": return "No protected files changed"
        case "restored": return "Scout put a protected file back"
        case "unrestored": return "A protected file changed and couldn't be put back"
        case "error": return "Couldn't check protected files"
        case "unchecked": return "Protected files weren't checked"
        case nil: return nil
        case let other?: return "Protected files: \(humanize(other))"
        }
    }

    static func integrityIsConcern(_ raw: String?) -> Bool {
        guard let raw else { return false }
        return raw != "clean"
    }

    // MARK: Findings

    /// Run-health severities, worst first. An unknown severity sorts with notes.
    enum Group: Int, CaseIterable, Comparable, Sendable {
        case red, yellow, note
        static func < (a: Group, b: Group) -> Bool { a.rawValue < b.rawValue }
        init(severity: String) {
            switch severity {
            case "red": self = .red
            case "yellow": self = .yellow
            default: self = .note
            }
        }
        var title: String {
            switch self {
            case .red: return "Needs fixing"
            case .yellow: return "Worth a look"
            case .note: return "Everything else"
            }
        }
    }

    static func overall(_ severity: DoctorReport.Severity?) -> String {
        switch severity {
        case .green: return "Scheduled runs are healthy"
        case .yellow: return "Scheduled runs need a look"
        case .red: return "Scheduled runs need fixing"
        case nil: return "Run health unknown"
        }
    }

    // MARK: Tables (C4's wording)

    struct PlainReason: Equatable, Sendable {
        let title: String
        let why: String
        let fix: String?
    }

    /// Lower-cased with `_` dropped, so `rateLimited` and `rate_limited` share an entry.
    static func normalized(_ raw: String) -> String {
        raw.lowercased().replacingOccurrences(of: "_", with: "")
    }

    /// `skipped_gates` → "Skipped gates": never the raw key.
    static func humanize(_ raw: String) -> String {
        var words: [String] = []
        var current = ""
        for ch in raw {
            if ch == "_" || ch == "-" || ch == " " {
                if !current.isEmpty { words.append(current); current = "" }
            } else if ch.isUppercase, !current.isEmpty {
                words.append(current); current = String(ch)
            } else {
                current.append(ch)
            }
        }
        if !current.isEmpty { words.append(current) }
        let sentence = words.map { $0.lowercased() }.joined(separator: " ")
        return sentence.prefix(1).uppercased() + sentence.dropFirst()
    }

    static let statuses: [String: String] = [
        "scheduled": "Scheduled",
        "running": "Running",
        "success": "Finished",
        "ok": "Finished",
        "failure": "Failed",
        "failed": "Failed",
        "timeout": "Stopped: took too long",
        "orphaned": "Didn't finish",
        "ratelimited": "Paused: usage limit",
        "skippedbudget": "Skipped: over budget",
        "skippedconcurrency": "Skipped: another run was going",
        "skippedlock": "Skipped: another run was going",
        "skippedredundant": "Skipped: already done",
        "skippedgates": "Skipped: privacy checks didn't load",
        "skippedsandbox": "Skipped: couldn't protect this run",
        "skippedruntime": "Skipped: agent not allowed",
        "skippedlease": "Skipped: another app was writing",
        "skippedtokenceiling": "Skipped: daily usage cap",
    ]

    private static let statusReasons: [String: String] = [
        "skippedbudget": "Running it would have gone over the budget you set.",
        "skippedconcurrency": "Another Scout run was still going, so this one waited for the next time.",
        "skippedlock": "Another Scout run was still going, so this one waited for the next time.",
        "skippedredundant": "An earlier run already covered this time.",
        "skippedgates": "Scout's privacy checks didn't load for this version of Claude Code, so the run didn't start.",
        "skippedsandbox": "Scout couldn't turn on write protection for this run, so it didn't start.",
        "skippedruntime": "The agent this schedule uses isn't allowed to run, or it's past its daily limit.",
        "skippedlease": "Another app was writing to the Scout folder, so this run waited.",
        "skippedtokenceiling": "The agent reached its daily usage cap.",
    ]

    private static let failureClasses: [String: PlainReason] = [
        "oauthexpired": PlainReason(
            title: "Signed out of Claude",
            why: "Scheduled runs can't start until Claude Code is signed in again.",
            fix: "Open Claude Code in a terminal and sign in."),
        "network": PlainReason(
            title: "Network problem",
            why: "The run couldn't reach Claude or a connected service.",
            fix: "Check your internet connection. Scout tries again at the next scheduled time."),
        "gitlock": PlainReason(
            title: "Scout folder was busy",
            why: "Another program was saving changes to the Scout folder at the same time.",
            fix: "This usually clears by itself. If it keeps happening, close other apps that save to the folder."),
        "ratelimited": PlainReason(
            title: "Usage limit reached",
            why: "Your Claude plan's usage limit was reached, so the run stopped.",
            fix: "Nothing to do: Scout tries again after the limit resets."),
        "budget": PlainReason(
            title: "Over budget",
            why: "The run used up the budget set for it.",
            fix: "Raise the budget in Settings ▸ Budget, or leave it: the next run starts with a fresh budget."),
        "timeout": PlainReason(
            title: "Took too long",
            why: "The run reached its time limit and Scout stopped it.",
            fix: "If this keeps happening, give this schedule a longer time limit."),
        "leaseheld": PlainReason(
            title: "Another app was writing",
            why: "Another app was writing to the Scout folder, so the run waited.",
            fix: "Nothing to do: the next run tries again."),
        "leaseunavailable": PlainReason(
            title: "Couldn't check who is writing",
            why: "Scout couldn't check whether another app was writing to the Scout folder, so the run waited.",
            fix: "If this keeps happening, check Settings ▸ Engine."),
        "gatesunsafeendpoint": PlainReason(
            title: "Privacy checks didn't load",
            why: "Scout's privacy checks didn't load, so the run didn't start.",
            fix: "Update Claude Code and Scout, then check Settings ▸ Engine."),
        "gatesunsafelauncher": PlainReason(
            title: "Privacy checks didn't load",
            why: "Scout's privacy checks didn't load, so the run didn't start.",
            fix: "Update Claude Code and Scout, then check Settings ▸ Engine."),
        "unknown": PlainReason(
            title: "Unrecognised error",
            why: "The run stopped with an error Scout doesn't recognise.",
            fix: "Open the run's log for details."),
    ]
}
