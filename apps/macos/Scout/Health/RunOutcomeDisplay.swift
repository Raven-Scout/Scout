import Foundation

/// The Health panel's own wording: write protection (the sandbox), the
/// integrity check, finding groups and the overall headline. Outcomes and
/// failure classes are `DisplayName`'s (card C4) and problems are `Problem`'s;
/// this adds only what the run ledger has that nothing else shows. Engine
/// words ("sandbox", "seatbelt", "gates", "lease") never reach the owner.
nonisolated enum RunOutcomeDisplay {

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
        case let other?: return "Write protection: \(DisplayName.humanize(other).lowercased())"
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
        case let other?: return "Protected files: \(DisplayName.humanize(other).lowercased())"
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
}

extension RunLedgerEntry {
    /// "Finished", "Stopped: took too long", "Skipped: privacy checks didn't
    /// load", in `DisplayName`'s words. A failed row says how it failed when
    /// the failure class has its own status (timeout, usage limit); an outcome
    /// this build doesn't know reads as "Failed", never as the raw value.
    var displayStatus: String {
        let known = DisplayName.runStatuses
        if outcome == "failed", let failureClass, known[DisplayName.normalized(failureClass)] != nil {
            return DisplayName.runStatus(failureClass)
        }
        return known[DisplayName.normalized(outcome)] != nil ? DisplayName.runStatus(outcome) : DisplayName.runStatus("failed")
    }

    /// What went wrong and what to do, or nil for a run that finished. A
    /// failure (or a skip with its own cause, like the privacy check's) is
    /// explained by its failure class; any other skip by its status.
    var problem: Problem? {
        guard outcome != "ok" else { return nil }
        let details = log.map { "Log: .scout-logs/\($0)" }
        if let failureClass, failureClass != "ok" {
            return Problem(failureClass: failureClass, details: details)
        }
        if isSkip {
            return Problem(runStatus: outcome, runName: DisplayName.slotKey(slot), details: details)
        }
        return Problem(failureClass: "unknown", details: details)
    }
}
