import SwiftUI

/// Container for the Table view. Header row + LazyVStack of `SlotTableRow`.
/// The parent (`SchedulesView`) supplies the filtered slot list, the
/// optional new-draft slot at the top, and the selection binding.
///
/// Column widths come from the shared `SchedulesColumns` spec so the header and
/// every row align (issue #14). A `GeometryReader` measures the available pane
/// width: the NAME column flexes to fill it, and when the pane is narrower than
/// `SchedulesColumns.minTotal` the whole table scrolls horizontally rather than
/// clipping against the window frame (issue #13).
struct SchedulesMasterTable: View {
    let slots: [Slot]
    let newDraftSlot: Slot?
    @Binding var selectedSlotKey: String?

    var body: some View {
        GeometryReader { geo in
            let available = geo.size.width
            let needsScroll = available < SchedulesColumns.minTotal
            let nameWidth = SchedulesColumns.nameWidth(forAvailable: available)

            if needsScroll {
                ScrollView(.horizontal, showsIndicators: true) {
                    table(nameWidth: nameWidth)
                        .frame(width: SchedulesColumns.minTotal, alignment: .leading)
                }
            } else {
                table(nameWidth: nameWidth)
            }
        }
    }

    private func table(nameWidth: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            headerRow(nameWidth: nameWidth)
            Divider().background(DS.Rule.hard)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if let draft = newDraftSlot {
                        row(for: draft, nameWidth: nameWidth)
                        Divider().background(DS.Rule.soft)
                    }
                    ForEach(slots) { slot in
                        row(for: slot, nameWidth: nameWidth)
                        Divider().background(DS.Rule.soft)
                    }
                }
            }
        }
    }

    private func headerRow(nameWidth: CGFloat) -> some View {
        HStack(spacing: SchedulesColumns.spacing) {
            headerCell("NAME").frame(width: nameWidth, alignment: .leading)
            headerCell("TYPE").frame(width: SchedulesColumns.type, alignment: .leading)
            headerCell("TIME").frame(width: SchedulesColumns.time, alignment: .leading)
            headerCell("DAYS").frame(width: SchedulesColumns.days, alignment: .leading)
            headerCell("ON MISS").frame(width: SchedulesColumns.onMiss, alignment: .leading)
            headerCell("COOLDOWN").frame(width: SchedulesColumns.cooldown, alignment: .leading)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, SchedulesColumns.hPadding)
    }

    private func headerCell(_ text: String) -> some View {
        Text(text)
            .font(DS.sans(10, weight: .medium))
            .tracking(1)
            .foregroundStyle(DS.Ink.p4)
    }

    @ViewBuilder
    private func row(for slot: Slot, nameWidth: CGFloat) -> some View {
        let isSelected = selectedSlotKey == slot.key
        SlotTableRow(slot: slot, isSelected: isSelected, nameWidth: nameWidth)
            .contentShape(Rectangle())
            .onTapGesture { selectedSlotKey = slot.key }
    }
}
