import Foundation
import Testing
@testable import Scout

/// Pins the fix for switching slots on the Schedules page with unsaved
/// edits: the slot form is torn down on every switch, so its draft lives in
/// `SlotDraftStore` and comes back when the slot is reopened, rebased onto
/// the slot as it is then. The quit prompt counts these drafts like KB ones.
@Suite("Slot draft safety")
@MainActor
struct SlotDraftSafetyTests {
    private static let morning = Slot(
        key: "morning-briefing",
        type: .briefing,
        runner: "run-scout.sh",
        firesAtLocal: "08:00",
        weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
        missedWindowHours: 4,
        onMiss: .fire,
        cooldownMinutes: 60
    )
    private static let evening = Slot(
        key: "evening-run",
        type: .consolidation,
        runner: "run-scout.sh",
        firesAtLocal: "18:00",
        weekdays: ["Mon", "Wed", "Fri"],
        missedWindowHours: 2,
        onMiss: .skip,
        cooldownMinutes: 30
    )

    /// The morning slot with `edit` applied (Slot's fields are constants).
    private static func morningChanged(_ edit: (inout SlotDraft) -> Void) -> Slot {
        var draft = SlotDraft(from: morning)
        edit(&draft)
        return draft.toSlot()
    }

    /// The morning slot with its time changed in the form.
    private func editedMorning(time: String = "07:30") -> SlotDraft {
        var draft = SlotDraft(from: Self.morning)
        draft.firesAtLocal = time
        return draft
    }

    // MARK: - Store

    @Test("a dirty draft is kept per slot; a clean one is dropped")
    func storeKeepsOnlyDirtyDrafts() {
        let store = SlotDraftStore()
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)
        #expect(store.hasDraft(for: Self.morning.key))
        #expect(!store.hasDraft(for: Self.evening.key))
        #expect(store.unsavedSlotKeys == ["morning-briefing"])

        // Revert: the form's draft matches the slot again.
        store.update(SlotDraft(from: Self.morning), baseline: Self.morning, for: Self.morning.key)
        #expect(!store.hasDraft(for: Self.morning.key))
        #expect(!store.hasUnsavedDrafts)
    }

    // MARK: - Switching slots

    @Test("switching away and back restores the unsaved edit")
    func switchingKeepsDraft() {
        let store = SlotDraftStore()
        // Edit the morning slot, then pick the evening slot: the morning
        // form goes away, its draft stays in the store.
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)
        let other = SlotEditForm.initialDraft(live: Self.evening, stashed: store.entry(for: Self.evening.key))
        #expect(other.draft == SlotDraft(from: Self.evening))

        // Back to the morning slot: the edit is there, still unsaved.
        let back = SlotEditForm.initialDraft(live: Self.morning, stashed: store.entry(for: Self.morning.key))
        #expect(back.draft == editedMorning())
        #expect(back.draft.isDirty(against: Self.morning))
        #expect(back.conflicts.isEmpty)
    }

    @Test("a restored draft takes changes made elsewhere to fields the user didn't touch")
    func restoredDraftRebases() {
        let changed = Self.morningChanged({ $0.cooldownMinutes = 90 })  // changed in schedule.yaml meanwhile
        let stash = SlotFormDraft(draft: editedMorning(), baseline: Self.morning)

        let restored = SlotEditForm.initialDraft(live: changed, stashed: stash)
        #expect(restored.draft.firesAtLocal == "07:30")      // the user's edit
        #expect(restored.draft.cooldownMinutes == 90)        // not written back over
        #expect(restored.conflicts.isEmpty)
    }

    @Test("a field changed on both sides keeps the user's value and is named")
    func restoredDraftNamesConflicts() {
        let changed = Self.morningChanged({ $0.firesAtLocal = "09:15" })
        let stash = SlotFormDraft(draft: editedMorning(), baseline: Self.morning)

        let restored = SlotEditForm.initialDraft(live: changed, stashed: stash)
        #expect(restored.draft.firesAtLocal == "07:30")
        #expect(restored.conflicts == ["Time"])
    }

    @Test("the same change made on both sides is not a conflict")
    func sameChangeIsNotAConflict() {
        let changed = Self.morningChanged({ $0.firesAtLocal = "07:30" })
        let rebased = editedMorning().rebased(from: Self.morning, onto: changed)
        #expect(rebased.conflicts.isEmpty)
        #expect(!rebased.draft.isDirty(against: changed))
    }

    @Test("a clean stash is ignored and the slot wins")
    func cleanStashIgnored() {
        let stash = SlotFormDraft(draft: SlotDraft(from: Self.morning), baseline: Self.morning)
        let changed = Self.morningChanged({ $0.cooldownMinutes = 15 })
        #expect(SlotEditForm.initialDraft(live: changed, stashed: stash).draft == SlotDraft(from: changed))
    }

    @Test("an undone save brings the open form back to the slot instead of leaving a stale draft")
    func undoRebasesCleanForm() {
        let saved = Self.morningChanged({ $0.firesAtLocal = "07:30" })
        // The form shows the saved slot, clean; undo restores the original.
        let rebased = SlotDraft(from: saved).rebased(from: saved, onto: Self.morning)
        #expect(rebased.draft == SlotDraft(from: Self.morning))
        #expect(rebased.conflicts.isEmpty)
    }

    // MARK: - Reconciling with the schedule

    @Test("a saved draft is dropped when the schedule reloads, even with its form closed")
    func reconcileDropsSavedDraft() {
        let store = SlotDraftStore()
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)
        store.reconcile(with: [editedMorning().toSlot(), Self.evening])
        #expect(!store.hasDraft(for: Self.morning.key))
    }

    @Test("a draft the schedule doesn't match yet survives a reload")
    func reconcileKeepsUnsavedDraft() {
        let store = SlotDraftStore()
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)
        let changed = Self.morningChanged({ $0.cooldownMinutes = 90 })
        store.reconcile(with: [changed, Self.evening])
        #expect(store.hasDraft(for: Self.morning.key))
    }

    @Test("a deleted slot's draft is dropped and reported; the unsaved new slot's is kept")
    func reconcileDropsMissingSlotsButKeepsNewSlot() {
        let store = SlotDraftStore()
        let newSlot = SchedulesView.makeNewDraftSlot(key: "new-slot-1")
        store.pendingNewSlot = newSlot
        var newDraft = SlotDraft(from: newSlot)
        newDraft.key = "night-review"
        store.update(newDraft, baseline: newSlot, for: newSlot.key)
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)

        let dropped = store.reconcile(with: [Self.evening])
        #expect(dropped == ["morning-briefing"])
        #expect(store.droppedSlotNames == ["morning-briefing"])
        #expect(!store.hasDraft(for: Self.morning.key))
        #expect(store.hasDraft(for: newSlot.key))
        // The quit prompt names the new slot by the key typed into it.
        #expect(store.unsavedSlotKeys == ["night-review"])

        store.clearDropped()
        #expect(store.droppedSlotNames.isEmpty)
    }

    @Test("a slot dropped twice is named once")
    func droppedNamesDeduplicated() {
        let store = SlotDraftStore()
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)
        store.reconcile(with: [Self.evening])
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)
        store.reconcile(with: [Self.evening])
        #expect(store.droppedSlotNames == ["morning-briefing"])
    }

    @Test("a saved new slot is finished in the store: no ghost new slot comes back")
    func finishNewSlotClearsPending() {
        let store = SlotDraftStore()
        let newSlot = SchedulesView.makeNewDraftSlot(key: "new-slot-1")
        store.pendingNewSlot = newSlot
        var draft = SlotDraft(from: newSlot)
        draft.key = "night-review"
        store.update(draft, baseline: newSlot, for: newSlot.key)

        store.finishNewSlot(key: newSlot.key)
        #expect(store.pendingNewSlot == nil)
        #expect(!store.hasUnsavedDrafts)

        // A different pending slot (added after) is left alone.
        let next = SchedulesView.makeNewDraftSlot(key: "new-slot-2")
        store.pendingNewSlot = next
        store.finishNewSlot(key: newSlot.key)
        #expect(store.pendingNewSlot == next)
    }

    @Test("a saved draft is dropped quietly, not reported as lost")
    func reconcileSavedDraftNotReported() {
        let store = SlotDraftStore()
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)
        #expect(store.reconcile(with: [editedMorning().toSlot()]).isEmpty)
        #expect(store.droppedSlotNames.isEmpty)
    }

    @Test("a draft set aside for a delete the user chose isn't reported; a failed delete puts it back")
    func deleteSetsDraftAside() {
        let store = SlotDraftStore()
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)
        let setAside = store.entry(for: Self.morning.key)
        store.discard(for: Self.morning.key)
        #expect(store.reconcile(with: [Self.evening]).isEmpty)

        store.restore(setAside, for: Self.morning.key)
        #expect(store.entry(for: Self.morning.key) == SlotFormDraft(draft: editedMorning(), baseline: Self.morning))
    }

    @Test("the dropped-edits notice names the slots")
    func droppedEditsText() {
        #expect(SchedulesView.droppedEditsText(["morning-briefing"])
            == "Unsaved edits to morning-briefing were dropped: the slot is no longer in the schedule.")
        #expect(SchedulesView.droppedEditsText(["evening-run", "morning-briefing"])
            == "Unsaved edits to evening-run, morning-briefing were dropped: the slots are no longer in the schedule.")
    }

    // MARK: - Weekday order

    /// A slot whose weekdays the file lists out of Mon–Sun order, as a
    /// hand-edited schedule.yaml can (the engine keeps the file's order).
    private static let handOrdered = Slot(
        key: "weekly-review",
        type: .research,
        runner: "run-research.sh",
        firesAtLocal: "16:00",
        weekdays: ["Fri", "Mon"],
        missedWindowHours: 4,
        onMiss: .skip,
        cooldownMinutes: 120
    )

    @Test("weekday order alone doesn't make a slot dirty")
    func weekdayOrderIsNotAnEdit() {
        let draft = SlotDraft(from: Self.handOrdered)
        #expect(!draft.isDirty(against: Self.handOrdered))
        #expect(draft.toSlot().weekdays == ["Mon", "Fri"])  // saves in Mon–Sun order

        let store = SlotDraftStore()
        store.update(draft, baseline: Self.handOrdered, for: Self.handOrdered.key)
        #expect(!store.hasUnsavedDrafts)
    }

    @Test("a saved edit to an out-of-order slot settles on reload")
    func weekdayOrderSavedDraftSettles() {
        let store = SlotDraftStore()
        var draft = SlotDraft(from: Self.handOrdered)
        draft.cooldownMinutes = 60
        store.update(draft, baseline: Self.handOrdered, for: Self.handOrdered.key)
        #expect(store.hasDraft(for: Self.handOrdered.key))

        // The engine reports the saved slot (written Mon–Sun); the file's
        // order could come back either way.
        let savedSlot = draft.toSlot()
        store.reconcile(with: [savedSlot])
        #expect(!store.hasDraft(for: Self.handOrdered.key))
    }

    // MARK: - Conflict notes

    @Test("conflicts on an optional field and on weekdays are named")
    func conflictsOnOptionalAndWeekdays() {
        var mine = SlotDraft(from: Self.morning)
        mine.model = "sonnet"
        mine.weekdays = ["Mon", "Wed"]
        let theirs = Self.morningChanged({ $0.model = "haiku"; $0.weekdays = ["Tue"] })
        let rebased = mine.rebased(from: Self.morning, onto: theirs)
        #expect(rebased.conflicts == ["Weekdays", "Model"])
        #expect(rebased.draft.model == "sonnet")
        #expect(rebased.draft.weekdays == ["Mon", "Wed"])
    }

    @Test("a conflict note survives a later change and a trip through the store")
    func conflictNoteIsSticky() {
        let theirs = Self.morningChanged({ $0.firesAtLocal = "09:15" })
        let first = editedMorning().rebased(from: Self.morning, onto: theirs)
        let note = SlotDraft.stickyConflicts(previous: [], new: first.conflicts, draft: first.draft, live: theirs)
        #expect(note == ["Time"])

        // Another change elsewhere (cooldown) brings no new conflict; the
        // Time note stays.
        let later = Self.morningChanged({ $0.firesAtLocal = "09:15"; $0.cooldownMinutes = 90 })
        let second = first.draft.rebased(from: theirs, onto: later)
        #expect(second.conflicts.isEmpty)
        #expect(SlotDraft.stickyConflicts(previous: note, new: second.conflicts,
                                          draft: second.draft, live: later) == ["Time"])

        // Stashed with the draft, it comes back when the slot reopens.
        let stash = SlotFormDraft(draft: second.draft, baseline: later, conflicts: ["Time"])
        #expect(SlotEditForm.initialDraft(live: later, stashed: stash).conflicts == ["Time"])
    }

    @Test("a conflict settles when the draft matches the file again")
    func conflictNoteSettles() {
        let theirs = Self.morningChanged({ $0.firesAtLocal = "09:15" })
        var draft = editedMorning()
        draft.firesAtLocal = "09:15"  // the user types the file's value back
        #expect(SlotDraft.stickyConflicts(previous: ["Time"], new: [], draft: draft, live: theirs).isEmpty)
    }

    // MARK: - Quit prompt names

    @Test("a new slot with a blank key is named 'a new slot'")
    func blankKeyName() {
        let store = SlotDraftStore()
        let newSlot = SchedulesView.makeNewDraftSlot(key: "new-slot-1")
        store.pendingNewSlot = newSlot
        var draft = SlotDraft(from: newSlot)
        draft.key = ""
        store.update(draft, baseline: newSlot, for: newSlot.key)
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)
        #expect(store.unsavedSlotKeys == ["a new slot", "morning-briefing"])
    }

    @Test("an untouched new slot holds no edits, so the quit prompt doesn't count it")
    func untouchedNewSlotNotCounted() {
        let store = SlotDraftStore()
        let newSlot = SchedulesView.makeNewDraftSlot(key: "new-slot-1")
        store.pendingNewSlot = newSlot
        store.update(SlotDraft(from: newSlot), baseline: newSlot, for: newSlot.key)
        #expect(!store.hasUnsavedDrafts)
    }

    // MARK: - Closing the pane

    @Test("the pane still won't close over a dirty slot or a new slot")
    func paneStaysOpenOverUnsavedEdits() {
        let pane = DetailPaneLayout.shown(width: 360, closable: true)
        #expect(!SchedulesView.canCloseDetail(pane: pane, isNewDraft: false, isDirty: true))
        #expect(!SchedulesView.canCloseDetail(pane: pane, isNewDraft: true, isDirty: false))
        #expect(SchedulesView.canCloseDetail(pane: pane, isNewDraft: false, isDirty: false))
    }

    // MARK: - Quit prompt

    @Test("quitting with a dirty slot form asks, naming the slot")
    func quitMessageNamesSlots() {
        #expect(AppDelegate.unsavedDraftsMessage(notes: [], slots: ["morning-briefing"])
            == "1 schedule slot has unsaved edits (morning-briefing). Quitting discards them.")
        #expect(AppDelegate.unsavedDraftsMessage(notes: ["alex.md"], slots: ["evening-run", "morning-briefing"])
            == "1 knowledge-base note has unsaved edits (alex.md), and 2 schedule slots have unsaved edits (evening-run, morning-briefing). Quitting discards them.")
    }

    @Test("KB-only quit wording is unchanged")
    func quitMessageKBOnlyUnchanged() {
        #expect(AppDelegate.unsavedDraftsMessage(notes: ["alex.md"], slots: [])
            == AppDelegate.unsavedDraftsMessage(["alex.md"]))
    }
}
