import SwiftUI

/// Detail pane in the master/detail layout. Wraps `SlotEditForm` for the
/// currently-selected slot; renders an empty-state when nothing is
/// selected. The parent (SchedulesView) decides what slot — including a
/// new draft — gets passed in.
struct SchedulesDetailPane: View {
    let slot: Slot?
    let isNewDraft: Bool
    let onSave: (Slot) async -> Void
    let onDelete: () async -> Void
    let onFireNow: (String) async -> Void
    let onRevertNewDraft: (() -> Void)?

    var body: some View {
        if let slot {
            content(for: slot)
        } else {
            emptyState
        }
    }

    @ViewBuilder
    private func content(for slot: Slot) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            header(for: slot)
            Divider().background(DS.Rule.soft)
            ScrollView {
                SlotEditForm(
                    liveSlot: slot,
                    isNewDraft: isNewDraft,
                    onSave: onSave,
                    onDelete: onDelete,
                    onFireNow: onFireNow,
                    onRevertNewDraft: onRevertNewDraft
                )
                .id(slot.key)
            }
        }
    }

    private func header(for slot: Slot) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(slot.key)
                .font(DS.mono(15, weight: .semibold))
                .foregroundStyle(DS.Ink.p1)
            TypePill(type: slot.type)
            Spacer()
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "cursorarrow.click")
                .font(.system(size: 36))
                .foregroundStyle(DS.Ink.p4)
            Text("Pick a slot to edit")
                .font(DS.serif(18, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
            Text("Click a row in the list to edit its time, weekdays, cooldown, and other settings.")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }
}
