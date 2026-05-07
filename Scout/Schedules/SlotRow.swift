import SwiftUI

/// Container that switches between SlotSummaryRow (collapsed) and
/// SlotEditForm (expanded). Tap on the summary toggles expansion.
struct SlotRow: View {
    let slot: Slot
    let isExpanded: Bool
    let isNewDraft: Bool
    let hasDirtyDraft: Bool
    let onToggleExpand: () -> Void
    let onSave: (Slot) async -> Void
    let onDelete: () async -> Void
    let onFireNow: (String) async -> Void
    let onRevertNewDraft: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onToggleExpand) {
                SlotSummaryRow(slot: slot, hasDirtyDraft: hasDirtyDraft, isExpanded: isExpanded)
            }
            .buttonStyle(.plain)

            if isExpanded {
                SlotEditForm(
                    liveSlot: slot,
                    isNewDraft: isNewDraft,
                    onSave: onSave,
                    onDelete: onDelete,
                    onFireNow: onFireNow,
                    onRevertNewDraft: onRevertNewDraft
                )
                .transition(.opacity.combined(with: .move(edge: .top)))
            }

            Divider()
        }
    }
}
