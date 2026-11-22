import SwiftUI

/// Container for the Table view. Header row + LazyVStack of `SlotTableRow`.
/// The parent (`SchedulesView`) supplies the filtered slot list, the
/// optional new-draft slot at the top, and the selection binding.
///
/// Column widths come from the shared `SchedulesColumns` spec so the header and
/// every row align (issue #14). They widen with the Text size, and a
/// `GeometryReader` measures the available pane width: the NAME column flexes
/// to fill it, the days column drops its word when the words don't fit, and a
/// pane too narrow for a row of columns shows one card per schedule instead of
/// scrolling sideways (issue #13, UI audit T4).
struct SchedulesMasterTable: View {
    let slots: [Slot]
    let newDraftSlot: Slot?
    @Binding var selectedSlotKey: String?
    /// Run-health warnings by slot key (`RunOutcomeDisplay.slotWarnings`).
    var warnings: [String: [RunOutcomeDisplay.SlotWarning]] = [:]

    /// The table's keyboard focus. A row is a plain button, which doesn't
    /// take focus on a click, so the click sets it: the arrows work at once.
    @FocusState private var tableFocused: Bool

    /// The rows in the order drawn: the new draft first, then the slots. The
    /// arrow keys walk this (UI audit #57); the Cards view shares it.
    static func keyboardOrder(slots: [Slot], newDraftSlot: Slot?) -> [String] {
        (newDraftSlot.map { [$0.key] } ?? []) + slots.map(\.key)
    }

    var body: some View {
        // Reading the scale here re-lays the table out when Text size changes.
        let columns = SchedulesColumns(scale: TextScale.shared.factor)
        GeometryReader { geo in
            let available = geo.size.width
            let mode = columns.mode(forAvailable: available)
            if mode == .stacked {
                SchedulesMasterCards(slots: slots, newDraftSlot: newDraftSlot,
                                     selectedSlotKey: $selectedSlotKey, warnings: warnings,
                                     twentyFourHour: true)
            } else {
                table(columns, mode: mode, nameWidth: columns.nameWidth(forAvailable: available, mode: mode))
            }
        }
    }

    private func table(_ columns: SchedulesColumns, mode: SchedulesColumns.Mode, nameWidth: CGFloat) -> some View {
        // The row the ring marks while the table has the keys (see `SchedulesMasterCards`).
        let ringed = KeyboardListNavigation.ringed(selectedSlotKey, in: Self.keyboardOrder(slots: slots, newDraftSlot: newDraftSlot))
        return VStack(alignment: .leading, spacing: 0) {
            headerRow(columns, mode: mode, nameWidth: nameWidth)
            Divider().background(DS.Rule.hard)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        if let draft = newDraftSlot {
                            row(for: draft, columns: columns, mode: mode, nameWidth: nameWidth, ringed: ringed)
                            Divider().background(DS.Rule.soft)
                        }
                        ForEach(slots) { slot in
                            row(for: slot, columns: columns, mode: mode, nameWidth: nameWidth, ringed: ringed)
                            Divider().background(DS.Rule.soft)
                        }
                    }
                }
                // ↑ ↓ Home End move the selection once the table has keyboard
                // focus; a click on a row gives it focus.
                .dsFocusContainer($tableFocused)
                .keyboardListNavigation(
                    when: tableFocused,
                    ids: { Self.keyboardOrder(slots: slots, newDraftSlot: newDraftSlot) },
                    selection: $selectedSlotKey
                ) { key in
                    proxy.scrollTo(key)
                }
            }
        }
    }

    private func headerRow(_ columns: SchedulesColumns, mode: SchedulesColumns.Mode, nameWidth: CGFloat) -> some View {
        HStack(spacing: SchedulesColumns.spacing) {
            Self.headerCell("NAME").frame(width: nameWidth, alignment: .leading)
            Self.headerCell("TYPE").frame(width: columns.type, alignment: .leading)
            Self.headerCell("TIME").frame(width: columns.time, alignment: .leading)
            Self.headerCell("DAYS").frame(width: columns.days(mode), alignment: .leading)
            Self.headerCell("ON MISS").frame(width: columns.onMiss, alignment: .leading)
            Self.headerCell("COOLDOWN").frame(width: columns.cooldown, alignment: .leading)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, SchedulesColumns.hPadding)
    }

    /// One line: a header never wraps a word ("COOLDOWN" did at Largest text).
    static func headerCell(_ text: String) -> some View {
        Text(text)
            .font(DS.font(.caption2, weight: .medium))
            .tracking(1)
            .foregroundStyle(DS.Ink.p4)
            .lineLimit(1)
    }

    @ViewBuilder
    private func row(for slot: Slot, columns: SchedulesColumns, mode: SchedulesColumns.Mode, nameWidth: CGFloat,
                     ringed: String?) -> some View {
        let isSelected = selectedSlotKey == slot.key
        // A button, not a tap gesture, so rows take keyboard focus and
        // VoiceOver reads each one as a selectable button.
        Button {
            selectedSlotKey = slot.key
            tableFocused = true
        } label: {
            SlotTableRow(slot: slot, isSelected: isSelected, nameWidth: nameWidth,
                         warnings: warnings[slot.key] ?? [], columns: columns, mode: mode)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plainHit)
        // Tab onto the table itself rings this row (T4).
        .dsCursorRing(ringed == slot.key, cornerRadius: 4)
        .accessibilitySelected(isSelected)
        .accessibilityIdentifier("schedules-row-\(slot.key)")
        .id(slot.key)
    }
}
