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
    var model: String?
    /// Carried through unchanged; the form shows it read-only (Phase 2 adds a picker).
    var agentRuntime: String?

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
        self.model = slot.model
        self.agentRuntime = slot.agentRuntime
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
            runtime: runtime,
            model: model,
            agentRuntime: agentRuntime
        )
    }

    /// Whether the draft differs from `live`. Compared as drafts, so weekdays
    /// compare as a set: the engine keeps schedule.yaml's order (a hand-edited
    /// `[Fri, Mon]`), while `toSlot()` writes Mon–Sun, and order alone must
    /// not make a slot look edited.
    func isDirty(against live: Slot) -> Bool {
        self != SlotDraft(from: live)
    }

    /// One editable field: its label in conflict notes, and how to compare
    /// and copy it. Every field but the key, which never moves.
    private struct Field {
        let label: String
        let equal: (SlotDraft, SlotDraft) -> Bool
        let copy: (inout SlotDraft, SlotDraft) -> Void

        init<T: Equatable>(_ path: WritableKeyPath<SlotDraft, T>, _ label: String) {
            self.label = label
            equal = { $0[keyPath: path] == $1[keyPath: path] }
            copy = { $0[keyPath: path] = $1[keyPath: path] }
        }
    }

    private static var fields: [Field] {
        [
            Field(\.type, "Type"),
            Field(\.runner, "Runner"),
            Field(\.firesAtLocal, "Time"),
            Field(\.weekdays, "Weekdays"),
            Field(\.missedWindowHours, "Missed window"),
            Field(\.onMiss, "On miss"),
            Field(\.cooldownMinutes, "Cooldown"),
            Field(\.budgetUsd, "Budget"),
            Field(\.tz, "Time zone"),
            Field(\.runtime, "Runtime"),
            Field(\.model, "Model"),
            Field(\.agentRuntime, "Agent"),
        ]
    }

    /// Labels of the fields where this draft and `other` differ, in form order.
    func differingFields(from other: SlotDraft) -> [String] {
        Self.fields.filter { !$0.equal(self, other) }.map(\.label)
    }

    /// This draft, typed against `baseline`, carried onto the slot as it is
    /// now (`live`): fields the user changed keep their value, the others
    /// take `live`'s, so a change made elsewhere (another device, undo, a
    /// reload) to a field the user didn't touch isn't written back over on
    /// save. `conflicts` names the fields both sides changed to different
    /// values; the user's value is kept there. The key never moves.
    func rebased(from baseline: Slot, onto live: Slot) -> (draft: SlotDraft, conflicts: [String]) {
        let base = SlotDraft(from: baseline)
        let theirs = SlotDraft(from: live)
        var out = self
        var conflicts: [String] = []
        for field in Self.fields {
            if field.equal(self, base) {
                field.copy(&out, theirs)
            } else if !field.equal(self, theirs) && !field.equal(base, theirs) {
                conflicts.append(field.label)
            }
        }
        return (out, conflicts)
    }

    /// The conflict note to show: earlier conflicts plus `new` ones, kept
    /// until Save or Revert, so a note survives a later change to the slot
    /// and a trip through `SlotDraftStore`. Only fields where the draft still
    /// differs from `live` stay (a save, or the user typing the file's value
    /// back, settles one). In form order.
    static func stickyConflicts(previous: [String], new: [String], draft: SlotDraft, live: Slot) -> [String] {
        let wanted = Set(previous).union(new)
        return draft.differingFields(from: SlotDraft(from: live)).filter { wanted.contains($0) }
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
