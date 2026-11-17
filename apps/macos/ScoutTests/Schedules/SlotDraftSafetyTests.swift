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

    @Test("a deleted slot's draft is dropped; the unsaved new slot's is kept")
    func reconcileDropsMissingSlotsButKeepsNewSlot() {
        let store = SlotDraftStore()
        let newSlot = SchedulesView.makeNewDraftSlot(key: "new-slot-1")
        var newDraft = SlotDraft(from: newSlot)
        newDraft.key = "night-review"
        store.update(newDraft, baseline: newSlot, for: newSlot.key)
        store.update(editedMorning(), baseline: Self.morning, for: Self.morning.key)

        store.reconcile(with: [Self.evening], keeping: [newSlot.key])
        #expect(!store.hasDraft(for: Self.morning.key))
        #expect(store.hasDraft(for: newSlot.key))
        // The quit prompt names the new slot by the key typed into it.
        #expect(store.unsavedSlotKeys == ["night-review"])
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
