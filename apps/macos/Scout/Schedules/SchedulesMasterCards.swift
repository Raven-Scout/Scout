import SwiftUI

/// Container for the Cards view. LazyVGrid of `SlotCard`. Adaptive columns
/// (240–320pt) flow 4-up at typical widths, 1-up at narrow.
struct SchedulesMasterCards: View {
    let slots: [Slot]
    let newDraftSlot: Slot?
    @Binding var selectedSlotKey: String?
    /// Run-health warnings by slot key (`RunOutcomeDisplay.slotWarnings`).
    var warnings: [String: [RunOutcomeDisplay.SlotWarning]] = [:]
    /// Set when the Table view stacks into cards: keep its 24-hour times.
    var twentyFourHour = false
    /// Set when the Table view stacks into cards: the table's focus, whose
    /// Tab stop and arrow keys sit around both of its layouts, so a click
    /// that stacks the table keeps the keys (UI audit T4). The grid then
    /// adds no stop or key handler of its own.
    var tableFocus: FocusState<Bool>.Binding?

    /// Keyboard focus, set by a click on a card (see `SchedulesMasterTable`).
    @FocusState private var cardsFocused: Bool

    /// Top-aligned, so a taller card in a row never pushes its neighbours' tops down.
    static let columns = [
        GridItem(.adaptive(minimum: minCardWidth, maximum: 320), spacing: 12, alignment: .top)
    ]
    /// The narrowest a card gets.
    static let minCardWidth: CGFloat = 240

    var body: some View {
        if let tableFocus {
            grid(focus: tableFocus)
        } else {
            ScrollViewReader { proxy in
                grid(focus: $cardsFocused)
                    // ↑ ↓ Home End step through the cards in reading order (#57):
                    // ↓ is the next card, which in a grid is the one to the right.
                    .dsFocusContainer($cardsFocused)
                    .keyboardListNavigation(
                        when: cardsFocused,
                        ids: { SchedulesMasterTable.keyboardOrder(slots: slots, newDraftSlot: newDraftSlot) },
                        selection: $selectedSlotKey,
                        announce: { SchedulesMasterTable.name(of: $0, slots: slots, newDraftSlot: newDraftSlot) }
                    ) { key in
                        proxy.scrollTo(key)
                    }
            }
        }
    }

    private func grid(focus: FocusState<Bool>.Binding) -> some View {
        // The card the ring marks while the grid has the keys: the selected one
        // while it's shown (a Type filter can hide it), else the first, which
        // ↓ picks from nothing (`KeyboardListNavigation.ringed`).
        let ringed = KeyboardListNavigation.ringed(
            selectedSlotKey, in: SchedulesMasterTable.keyboardOrder(slots: slots, newDraftSlot: newDraftSlot))
        return ScrollView {
            LazyVGrid(columns: Self.columns, alignment: .leading, spacing: 12) {
                if let draft = newDraftSlot {
                    cardButton(for: draft, ringed: ringed, focus: focus)
                }
                ForEach(slots) { slot in
                    cardButton(for: slot, ringed: ringed, focus: focus)
                }
            }
            .padding(16)
        }
    }

    @ViewBuilder
    private func cardButton(for slot: Slot, ringed: String?, focus: FocusState<Bool>.Binding) -> some View {
        let isSelected = selectedSlotKey == slot.key
        Button {
            selectedSlotKey = slot.key
            focus.wrappedValue = true
        } label: {
            SlotCard(slot: slot, isSelected: isSelected, warnings: warnings[slot.key] ?? [],
                     twentyFourHour: twentyFourHour)
        }
        .buttonStyle(.dsCard(cornerRadius: 10))
        // The grid's stop and arrows reach every card; a card isn't a stop of its own.
        .dsContainerItem()
        // Tab onto the grid itself rings this card (T4).
        .dsCursorRing(ringed == slot.key, cornerRadius: 10)
        .accessibilitySelected(isSelected)
        .accessibilityIdentifier("schedules-card-\(slot.key)")
        .id(slot.key)
    }
}
