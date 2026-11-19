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
            Field(\.type, SlotFieldLabel.kind),
            Field(\.runner, SlotFieldLabel.script),
            Field(\.firesAtLocal, SlotFieldLabel.time),
            Field(\.weekdays, SlotFieldLabel.weekdays),
            Field(\.missedWindowHours, SlotFieldLabel.catchUpWindow),
            Field(\.onMiss, SlotFieldLabel.ifAsleep),
            Field(\.cooldownMinutes, SlotFieldLabel.waitBetweenRuns),
            Field(\.budgetUsd, SlotFieldLabel.budget),
            Field(\.tz, SlotFieldLabel.timeZone),
            Field(\.runtime, SlotFieldLabel.runtime),
            Field(\.model, SlotFieldLabel.model),
            Field(\.agentRuntime, SlotFieldLabel.agent),
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
    /// Used by the form to disable the Save button. `existingKeys` are the
    /// schedules already saved; pass them for a new schedule so its name
    /// can't land on one of them (an existing schedule's own key is fine).
    /// Worded in the form's own field names (`SlotFieldLabel`), never the
    /// file's (`runner`, `cooldown_minutes`, `missed_window_hours`): UI
    /// audit #55.
    func firstError(existingKeys: Set<String> = []) -> String? {
        if let e = SlotDraft.validateSlotKey(key) { return e }
        if let e = SlotDraft.validateNewSlotKey(key, existingKeys: existingKeys) { return e }
        if let e = SlotDraft.validateFiresAtLocal(firesAtLocal) { return e }
        if let e = SlotDraft.validateWeekdays(Array(weekdays)) { return e }
        if let e = SlotDraft.validateScript(runner) { return e }
        if cooldownMinutes < 0 { return SlotValidationMessage.negativeWait }
        if missedWindowHours <= 0 { return SlotValidationMessage.catchUpTooShort }
        return nil
    }

    // MARK: - Static field validators (for unit tests + live form errors).

    static func validateSlotKey(_ s: String) -> String? {
        guard !s.isEmpty else { return SlotValidationMessage.emptyName }
        // kebab-case: lowercase letter, then lowercase letters/digits/hyphens.
        let re = try! NSRegularExpression(pattern: #"^[a-z][a-z0-9-]*$"#)
        let range = NSRange(s.startIndex..<s.endIndex, in: s)
        return re.firstMatch(in: s, range: range) == nil
            ? SlotValidationMessage.badName
            : nil
    }

    /// A new schedule's name must not be one already in the schedule: the
    /// engine reads schedule.yaml with a YAML loader that keeps the last of
    /// two same-named entries, so a duplicate would silently replace the
    /// saved schedule (UI audit #58).
    static func validateNewSlotKey(_ s: String, existingKeys: Set<String>) -> String? {
        existingKeys.contains(s) ? SlotValidationMessage.duplicateName(s) : nil
    }

    static func validateFiresAtLocal(_ s: String) -> String? {
        let re = try! NSRegularExpression(pattern: #"^([01]\d|2[0-3]):[0-5]\d$"#)
        let range = NSRange(s.startIndex..<s.endIndex, in: s)
        return re.firstMatch(in: s, range: range) == nil
            ? SlotValidationMessage.badTime
            : nil
    }

    static func validateWeekdays(_ days: [String]) -> String? {
        days.isEmpty ? SlotValidationMessage.noWeekdays : nil
    }

    static func validateScript(_ s: String) -> String? {
        s.trimmingCharacters(in: .whitespaces).isEmpty ? SlotValidationMessage.emptyScript : nil
    }

    // MARK: - Time of day (the form's time picker, UI audit #58)

    /// `firesAtLocal` as a time today, for the form's time picker, or nil
    /// when the file holds something that isn't a time. Accepts a missing
    /// leading zero ("8:00"), which a hand-edited file may have.
    static func date(fromFiresAt s: String, calendar: Calendar = .current, on day: Date = .now) -> Date? {
        let parts = s.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 2,
              let h = Int(parts[0]), let m = Int(parts[1]),
              (0...23).contains(h), (0...59).contains(m),
              parts[1].count == 2 else { return nil }
        return calendar.date(bySettingHour: h, minute: m, second: 0, of: day)
    }

    /// The `fires_at_local` value for a time the picker chose: always
    /// zero-padded 24-hour "HH:MM", whatever the user's clock format.
    static func firesAt(from date: Date, calendar: Calendar = .current) -> String {
        let c = calendar.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", c.hour ?? 0, c.minute ?? 0)
    }
}

/// What the schedule form says when a field can't be saved, in the form's
/// own words (`SlotFieldLabel`), so a message names the field the user can
/// see (UI audit #55).
nonisolated enum SlotValidationMessage {
    static let emptyName = "Enter a name"
    static let badName = "Use lowercase letters, digits and dashes, starting with a letter, like evening-research"
    static func duplicateName(_ key: String) -> String {
        "There's already a schedule called \(DisplayName.slotKey(key)). Pick another name."
    }
    static let badTime = "Pick a time"
    static let noWeekdays = "Pick at least one weekday"
    static let emptyScript = "\(SlotFieldLabel.script) can't be empty. Open Advanced and enter the script to start, usually run-scout.sh."
    static let negativeWait = "\(SlotFieldLabel.waitBetweenRuns) can't be less than 0 minutes"
    static let catchUpTooShort = "\(SlotFieldLabel.catchUpWindow) must be at least 1 hour"
}

/// The schedule form's field names (UI audit C4's wording), shared by the
/// form's headings and the conflict note that names fields, so the two
/// can't drift apart. Units stay with the form's headings.
nonisolated enum SlotFieldLabel {
    static let name = "Name"
    static let time = "Time"
    static let weekdays = "Weekdays"
    static let ifAsleep = "If the Mac was asleep"
    static let waitBetweenRuns = "Wait between runs"
    static let model = "Model"
    static let agent = "Agent"
    static let script = "Script"
    static let catchUpWindow = "Catch-up window"
    static let kind = "Kind of work"
    /// Not shown in the form; named only if it ever conflicts.
    static let budget = "Budget"
    static let timeZone = "Time zone"
    static let runtime = "Where it runs"
}
