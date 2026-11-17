import Foundation
import Combine

/// One slot form's unsaved state: what the user has set, and the slot as it
/// was when they set it. The baseline lets a restored draft be rebased onto
/// the slot as it is now (see `SlotDraft.rebased`), so a change made to the
/// slot elsewhere in the meantime isn't silently written back over.
struct SlotFormDraft: Equatable {
    var draft: SlotDraft
    var baseline: Slot
    /// Fields the form's conflict note names (see `SlotDraft.stickyConflicts`),
    /// kept so the note comes back with the draft.
    var conflicts: [String] = []

    var isDirty: Bool { draft.isDirty(against: baseline) }
}

/// Unsaved slot-form drafts, held for the app's lifetime so that picking
/// another slot, adding a slot or leaving the Schedules page never discards
/// edits (the form keeps its draft in `@State` and is torn down on every
/// switch). The form writes its state here on every change; reopening the
/// slot restores it. Only dirty drafts are kept. Same model as
/// `KBDraftStore`.
///
/// `ScheduleEditService` reconciles it after every successful load, on the
/// Schedules page or not, so a draft that was saved, or whose slot is gone,
/// never lingers. In memory only: `AppDelegate` asks before quitting while
/// any remain.
@MainActor
final class SlotDraftStore: ObservableObject {
    /// The store the app's single `ScheduleEditService` reconciles, the
    /// Schedules page uses, and `AppDelegate` checks before quitting.
    static let shared = SlotDraftStore()

    private var drafts: [String: SlotFormDraft] = [:]

    /// The "+ Add slot" row not yet saved, kept so leaving the page and
    /// coming back doesn't drop it (its form draft is keyed by its key).
    /// Reconciling always keeps its draft. An untouched new slot holds no
    /// edits, so the quit prompt doesn't count it.
    var pendingNewSlot: Slot?

    /// Names of slots whose unsaved edits `reconcile` dropped because the
    /// slot left the schedule, for the Schedules page to say so. Cleared
    /// when the page's notice is dismissed.
    @Published private(set) var droppedSlotNames: [String] = []

    /// Record the form's `draft` for the slot keyed `slotKey`, typed against
    /// `baseline`; a clean draft removes any entry.
    func update(_ draft: SlotDraft, baseline: Slot, conflicts: [String] = [], for slotKey: String) {
        let entry = SlotFormDraft(draft: draft, baseline: baseline, conflicts: conflicts)
        drafts[slotKey] = entry.isDirty ? entry : nil
    }

    func entry(for slotKey: String) -> SlotFormDraft? { drafts[slotKey] }

    /// Put an entry back as it was (a delete that failed after its draft was
    /// set aside).
    func restore(_ entry: SlotFormDraft?, for slotKey: String) {
        drafts[slotKey] = entry
    }

    /// Whether the slot keyed `slotKey` has unsaved edits.
    func hasDraft(for slotKey: String) -> Bool { drafts[slotKey] != nil }

    /// Drop the draft, e.g. when the user deletes the slot or reverts a new one.
    func discard(for slotKey: String) { drafts[slotKey] = nil }

    /// Bring the store in line with the schedule as just loaded (`slots`).
    /// A draft the slot now matches is dropped quietly: it was saved, even if
    /// its form closed before the save finished. A draft whose slot is gone
    /// (deleted outside the form, or an undone add) is dropped too, since
    /// nothing could save it, but not quietly: its name goes to
    /// `droppedSlotNames` and is returned. The pending new slot's draft is
    /// always kept.
    @discardableResult
    func reconcile(with slots: [Slot]) -> [String] {
        let live = Dictionary(slots.map { ($0.key, $0) }, uniquingKeysWith: { first, _ in first })
        var orphans: [String] = []
        for (key, entry) in drafts {
            if let slot = live[key] {
                if !entry.draft.rebased(from: entry.baseline, onto: slot).draft.isDirty(against: slot) {
                    drafts[key] = nil
                }
            } else if key != pendingNewSlot?.key {
                drafts[key] = nil
                orphans.append(Self.displayName(entry.draft.key))
            }
        }
        orphans.sort()
        if !orphans.isEmpty { droppedSlotNames.append(contentsOf: orphans) }
        return orphans
    }

    func clearDropped() { droppedSlotNames = [] }

    /// Slot names with unsaved drafts, sorted, for the quit prompt. A new
    /// slot is named by the key typed into it, or "a new slot" when that's
    /// blank.
    var unsavedSlotKeys: [String] {
        drafts.values.map { Self.displayName($0.draft.key) }.sorted()
    }

    var hasUnsavedDrafts: Bool { !drafts.isEmpty }

    nonisolated static func displayName(_ key: String) -> String {
        let trimmed = key.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? "a new slot" : trimmed
    }
}
