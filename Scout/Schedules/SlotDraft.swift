import Foundation

/// Mutable working copy of a Slot. The view edits this in @State; on Save,
/// the form serializes it back to a Slot via toSlot() and hands it to
/// ScheduleEditService.
struct SlotDraft: Equatable {
    var key: String
    var type: SlotType
    var runner: String
    var firesAtLocal: String
    var weekdays: Set<String>
    var missedWindowHours: Int
    var onMiss: OnMissPolicy
    var cooldownMinutes: Int
    var budgetUsd: Double?
    var tz: String?
    var runtime: SlotRuntime

    init(from slot: Slot) {
        self.key = slot.key
        self.type = slot.type
        self.runner = slot.runner
        self.firesAtLocal = slot.firesAtLocal
        self.weekdays = Set(slot.weekdays)
        self.missedWindowHours = slot.missedWindowHours
        self.onMiss = slot.onMiss
        self.cooldownMinutes = slot.cooldownMinutes
        self.budgetUsd = slot.budgetUsd
        self.tz = slot.tz
        self.runtime = slot.runtime
    }

    /// Materialize back to a Slot for the save path. Weekdays come out in
    /// canonical Mon-Sun order regardless of toggle order.
    func toSlot() -> Slot {
        let order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return Slot(
            key: key,
            type: type,
            runner: runner,
            firesAtLocal: firesAtLocal,
            weekdays: order.filter { weekdays.contains($0) },
            missedWindowHours: missedWindowHours,
            onMiss: onMiss,
            cooldownMinutes: cooldownMinutes,
            budgetUsd: budgetUsd,
            tz: tz,
            runtime: runtime
        )
    }

    func isDirty(against live: Slot) -> Bool {
        toSlot() != live
    }

    /// Returns the first per-field validation error, or nil if all clean.
    /// Used by the form to disable the Save button.
    var firstError: String? {
        if let e = SlotDraft.validateSlotKey(key) { return e }
        if let e = SlotDraft.validateFiresAtLocal(firesAtLocal) { return e }
        if let e = SlotDraft.validateWeekdays(Array(weekdays)) { return e }
        if runner.trimmingCharacters(in: .whitespaces).isEmpty {
            return "Runner can't be empty"
        }
        if cooldownMinutes < 0 { return "Cooldown must be >= 0" }
        if missedWindowHours <= 0 { return "Missed window must be > 0" }
        return nil
    }

    // MARK: - Static field validators (for unit tests + live form errors).

    static func validateSlotKey(_ s: String) -> String? {
        guard !s.isEmpty else { return "Slot key required" }
        // kebab-case: lowercase letter, then lowercase letters/digits/hyphens.
        let re = try! NSRegularExpression(pattern: #"^[a-z][a-z0-9-]*$"#)
        let range = NSRange(s.startIndex..<s.endIndex, in: s)
        return re.firstMatch(in: s, range: range) == nil
            ? "Slot key must be lowercase kebab-case (a-z, 0-9, hyphens; first char a letter)"
            : nil
    }

    static func validateFiresAtLocal(_ s: String) -> String? {
        let re = try! NSRegularExpression(pattern: #"^([01]\d|2[0-3]):[0-5]\d$"#)
        let range = NSRange(s.startIndex..<s.endIndex, in: s)
        return re.firstMatch(in: s, range: range) == nil
            ? "Time must be HH:MM (24-hour, leading zero required)"
            : nil
    }

    static func validateWeekdays(_ days: [String]) -> String? {
        days.isEmpty ? "Pick at least one weekday" : nil
    }
}
