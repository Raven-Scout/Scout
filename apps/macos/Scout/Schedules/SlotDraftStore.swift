import Foundation

/// One slot form's unsaved state: what the user has set, and the slot as it
/// was when they set it. The baseline lets a restored draft be rebased onto
/// the slot as it is now (see `SlotDraft.rebased`), so a change made to the
/// slot elsewhere in the meantime isn't silently written back over.
struct SlotFormDraft: Equatable {
    var draft: SlotDraft
    var baseline: Slot

    var isDirty: Bool { draft.isDirty(against: baseline) }
}

/// Unsaved slot-form drafts, held for the app's lifetime so that picking
/// another slot, adding a slot, or leaving the Schedules page never discards
/// edits (the form keeps its draft in `@State` and is torn down on every
/// switch). The form writes its state here on every change; reopening the
/// slot restores it. Only dirty drafts are kept. Same model as
/// `KBDraftStore`.
///
/// In memory only: `AppDelegate` asks before quitting while any remain.
@MainActor
final class SlotDraftStore {
    /// The store the Schedules page uses, and the one `AppDelegate` checks
    /// before quitting.
    static let shared = SlotDraftStore()

    private var drafts: [String: SlotFormDraft] = [:]

    /// The "+ Add slot" row not yet saved, kept so leaving the page and
    /// coming back doesn't drop it (its form draft is keyed by its key).
    var pendingNewSlot: Slot?

    /// Record the form's `draft` for the slot keyed `slotKey`, typed against
    /// `baseline`; a clean draft removes any entry.
    func update(_ draft: SlotDraft, baseline: Slot, for slotKey: String) {
        let entry = SlotFormDraft(draft: draft, baseline: baseline)
        drafts[slotKey] = entry.isDirty ? entry : nil
    }

    func entry(for slotKey: String) -> SlotFormDraft? { drafts[slotKey] }

    /// Whether the slot keyed `slotKey` has unsaved edits.
    func hasDraft(for slotKey: String) -> Bool { drafts[slotKey] != nil }

    /// Drop the draft, e.g. after it was saved or the slot was deleted.
    func discard(for slotKey: String) { drafts[slotKey] = nil }

    /// Bring the store in line with the schedule as just loaded (`slots`):
    /// drop a draft the slot now matches (it was saved, even if its form was
    /// closed before the save finished), and a draft whose slot is gone
    /// (deleted, or an undone add) unless its key is in `keeping` (the
    /// unsaved new slot): nothing could save it, and the quit prompt must
    /// not name a slot the page no longer shows.
    func reconcile(with slots: [Slot], keeping: Set<String> = []) {
        let live = Dictionary(slots.map { ($0.key, $0) }, uniquingKeysWith: { first, _ in first })
        drafts = drafts.filter { key, entry in
            guard let slot = live[key] else { return keeping.contains(key) }
            return entry.draft.rebased(from: entry.baseline, onto: slot).draft.isDirty(against: slot)
        }
    }

    /// Slot keys with unsaved drafts, sorted, for the quit prompt. A new slot
    /// is named by the key typed into its form.
    var unsavedSlotKeys: [String] {
        drafts.values.map(\.draft.key).sorted()
    }

    var hasUnsavedDrafts: Bool { !drafts.isEmpty }
}
