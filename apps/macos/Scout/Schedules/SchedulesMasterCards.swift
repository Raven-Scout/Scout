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

    /// Keyboard focus, set by a click on a card (see `SchedulesMasterTable`).
    @FocusState private var cardsFocused: Bool

    /// Top-aligned, so a taller card in a row never pushes its neighbours' tops down.
    static let columns = [
        GridItem(.adaptive(minimum: minCardWidth, maximum: 320), spacing: 12, alignment: .top)
    ]
    /// The narrowest a card gets.
    static let minCardWidth: CGFloat = 240

    var body: some View {
        // The card the ring marks while the grid has the keys: the selected one
        // while it's shown (a Type filter can hide it), else the first, which
        // ↓ picks from nothing (`KeyboardListNavigation.ringed`).
        let ringed = KeyboardListNavigation.ringed(
            selectedSlotKey, in: SchedulesMasterTable.keyboardOrder(slots: slots, newDraftSlot: newDraftSlot))
        ScrollViewReader { proxy in
            ScrollView {
                LazyVGrid(columns: Self.columns, alignment: .leading, spacing: 12) {
                    if let draft = newDraftSlot {
                        cardButton(for: draft, ringed: ringed)
                    }
                    ForEach(slots) { slot in
                        cardButton(for: slot, ringed: ringed)
                    }
                }
                .padding(16)
            }
            // ↑ ↓ Home End step through the cards in reading order (#57):
            // ↓ is the next card, which in a grid is the one to the right.
            .focusable()
            .focused($cardsFocused)
            .focusEffectDisabled()
            .dsFocusContainer(cardsFocused)
            .keyboardListNavigation(
                ids: { SchedulesMasterTable.keyboardOrder(slots: slots, newDraftSlot: newDraftSlot) },
                selection: $selectedSlotKey
            ) { key in
                proxy.scrollTo(key)
            }
        }
    }

    @ViewBuilder
    private func cardButton(for slot: Slot, ringed: String?) -> some View {
        let isSelected = selectedSlotKey == slot.key
        Button {
            selectedSlotKey = slot.key
            cardsFocused = true
        } label: {
            SlotCard(slot: slot, isSelected: isSelected, warnings: warnings[slot.key] ?? [],
                     twentyFourHour: twentyFourHour)
        }
        .buttonStyle(.dsCard(cornerRadius: 10))
        // Tab onto the grid itself rings this card; a card Tab lands on rings itself (T4).
        .dsCursorRing(ringed == slot.key, cornerRadius: 10)
        .accessibilitySelected(isSelected)
        .id(slot.key)
    }
}
