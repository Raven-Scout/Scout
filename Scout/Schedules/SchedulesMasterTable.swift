import SwiftUI

/// Container for the Table view. Header row + LazyVStack of `SlotTableRow`.
/// The parent (`SchedulesView`) supplies the filtered slot list, the
/// optional new-draft slot at the top, and the selection binding.
struct SchedulesMasterTable: View {
    let slots: [Slot]
    let newDraftSlot: Slot?
    @Binding var selectedSlotKey: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            headerRow
            Divider().background(DS.Rule.hard)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if let draft = newDraftSlot {
                        row(for: draft)
                        Divider().background(DS.Rule.soft)
                    }
                    ForEach(slots) { slot in
                        row(for: slot)
                        Divider().background(DS.Rule.soft)
                    }
                }
            }
        }
    }

    private var headerRow: some View {
        HStack(spacing: 16) {
            headerCell("NAME").frame(maxWidth: .infinity, alignment: .leading)
            headerCell("TYPE").frame(width: 140, alignment: .leading)
            headerCell("TIME").frame(width: 70, alignment: .leading)
            headerCell("DAYS").frame(width: 200, alignment: .leading)
            headerCell("ON MISS").frame(width: 90, alignment: .leading)
            headerCell("COOLDOWN").frame(width: 90, alignment: .leading)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 16)
    }

    private func headerCell(_ text: String) -> some View {
        Text(text)
            .font(DS.sans(10, weight: .medium))
            .tracking(1)
            .foregroundStyle(DS.Ink.p4)
    }

    @ViewBuilder
    private func row(for slot: Slot) -> some View {
        let isSelected = selectedSlotKey == slot.key
        Button {
            selectedSlotKey = slot.key
        } label: {
            SlotTableRow(slot: slot, isSelected: isSelected)
        }
        .buttonStyle(.plain)
    }
}
