import Foundation

/// A task's `/scout-plan` markers, written by the engine's
/// `scoutctl action-items set-estimate | set-block | set-actual`:
///
///     - [ ] [#TAG] **Task**
///       - estimate: 45m (raw: 30m, kind: deep)
///       - block: 2026-09-30 10:00-10:45 (event: <calendar event id>)
///       - actual: 1h (2026-09-30)
///
/// Like `snoozed-until`, these are machine metadata: the parser folds them
/// into `ActionTask.plan` instead of exposing them as comments. What counts
/// as a marker mirrors the engine's `plan_marks._mark_lines`: inside the
/// task's sub-list, the first line per key whose value parses. Any other
/// `- estimate:` line stays a comment. The engine's `--index` still counts
/// the markers, for every app version, so a comment write goes through
/// `engineCommentIndex`.
nonisolated struct TaskPlan: Equatable, Hashable, Sendable {
    var estimateMinutes: Int?
    var rawMinutes: Int?
    var kind: String?
    var block: PlannedBlock?
    var actualMinutes: Int?
    var actualDate: String?
    /// For each marker line, how many of the task's comments came before it,
    /// so `engineCommentIndex` can count the markers back in.
    var markerPositions: [Int] = []

    static let none = TaskPlan()

    var isEmpty: Bool { estimateMinutes == nil && block == nil && actualMinutes == nil }

    /// This plan with one marker line folded in, or nil when the line is not
    /// a marker: the value does not parse, or the key already has one (the
    /// first wins). The caller then leaves the line to the comment path.
    func merging(key: String, value: String, commentsBefore: Int = 0) -> TaskPlan? {
        var out = self
        switch key {
        case "estimate":
            guard estimateMinutes == nil,
                  let m = Self.estimateRe.firstMatch(in: value, range: Self.full(value)),
                  let minutes = Self.parseDuration(Self.group(value, m, 1)) else { return nil }
            out.estimateMinutes = minutes
            out.rawMinutes = Self.optionalGroup(value, m, 2).flatMap(Self.parseDuration)
            out.kind = Self.optionalGroup(value, m, 3)
        case "block":
            guard block == nil,
                  let m = Self.blockRe.firstMatch(in: value, range: Self.full(value)) else { return nil }
            out.block = PlannedBlock(
                date: Self.group(value, m, 1),
                start: Self.group(value, m, 2),
                end: Self.group(value, m, 3),
                eventID: Self.optionalGroup(value, m, 4)
            )
        case "actual":
            guard actualMinutes == nil,
                  let m = Self.actualRe.firstMatch(in: value, range: Self.full(value)),
                  let minutes = Self.parseDuration(Self.group(value, m, 1)) else { return nil }
            out.actualMinutes = minutes
            out.actualDate = Self.optionalGroup(value, m, 2)
        default:
            return nil
        }
        out.markerPositions.append(commentsBefore)
        return out
    }

    /// The `--index` `scoutctl delete-comment` / `edit-comment` expect for the
    /// comment the app shows at 1-based `index`: the engine counts the markers
    /// above it as comments, as every app before this one showed them.
    func engineCommentIndex(_ index: Int) -> Int {
        index + markerPositions.filter { $0 < index }.count
    }

    /// `"1h15m"` -> 75. Nil for anything that is not a positive h/m duration.
    static func parseDuration(_ text: String) -> Int? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty,
              let m = durationRe.firstMatch(in: trimmed, range: full(trimmed)) else { return nil }
        let hours = optionalGroup(trimmed, m, 1).flatMap { Int($0) }
        let mins = optionalGroup(trimmed, m, 2).flatMap { Int($0) }
        guard hours != nil || mins != nil else { return nil }
        let total = (hours ?? 0) * 60 + (mins ?? 0)
        return total > 0 ? total : nil
    }

    /// 75 -> `"1h15m"`, 60 -> `"1h"`, 45 -> `"45m"`.
    static func formatDuration(_ minutes: Int) -> String {
        let (h, m) = minutes.quotientAndRemainder(dividingBy: 60)
        switch (h, m) {
        case (0, _): return "\(m)m"
        case (_, 0): return "\(h)h"
        default: return "\(h)h\(m)m"
        }
    }

    // MARK: - Grammar (mirrors plugin/engine/scout/action_items/plan_marks.py)

    private static let durationRe = try! NSRegularExpression(
        pattern: #"^(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?$"#, options: [.caseInsensitive]
    )
    private static let estimateRe = try! NSRegularExpression(
        pattern: #"^(\S+)(?:\s+\((?:raw:\s*([^,\s)]+),\s*)?kind:\s*([a-z0-9-]+)\))?$"#
    )
    private static let blockRe = try! NSRegularExpression(
        pattern: #"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})-(\d{2}:\d{2})(?:\s+\(event:\s*([^)\s]+)\))?$"#
    )
    private static let actualRe = try! NSRegularExpression(
        pattern: #"^(\S+)(?:\s+\((\d{4}-\d{2}-\d{2})\))?$"#
    )

    private static func full(_ s: String) -> NSRange { NSRange(location: 0, length: (s as NSString).length) }

    private static func group(_ s: String, _ m: NSTextCheckingResult, _ i: Int) -> String {
        (s as NSString).substring(with: m.range(at: i))
    }

    private static func optionalGroup(_ s: String, _ m: NSTextCheckingResult, _ i: Int) -> String? {
        m.range(at: i).location == NSNotFound ? nil : group(s, m, i)
    }
}

/// A calendar block from a `- block:` marker. Times are the vault's local
/// wall-clock `HH:MM`, exactly as written. A reversed block still parses, as
/// in the engine, and has a negative length.
nonisolated struct PlannedBlock: Equatable, Hashable, Sendable {
    let date: String
    let start: String
    let end: String
    let eventID: String?

    var minutes: Int { Self.clock(end) - Self.clock(start) }

    private static func clock(_ hhmm: String) -> Int {
        let parts = hhmm.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 2 else { return 0 }
        return parts[0] * 60 + parts[1]
    }
}
