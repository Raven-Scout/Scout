import SwiftUI

/// Container for the Cards view. LazyVGrid of `SlotCard`. Adaptive columns
/// (240–320pt) flow 4-up at typical widths, 1-up at narrow.
struct SchedulesMasterCards: View {
    let slots: [Slot]
    let newDraftSlot: Slot?
    @Binding var selectedSlotKey: String?
    /// Run-health warnings by slot key (`RunOutcomeDisplay.slotWarnings`).
    var warnings: [String: [RunOutcomeDisplay.SlotWarning]] = [:]

    private let columns = [
        GridItem(.adaptive(minimum: 240, maximum: 320), spacing: 12)
    ]

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVGrid(columns: columns, alignment: .leading, spacing: 12) {
                    if let draft = newDraftSlot {
                        cardButton(for: draft)
                    }
                    ForEach(slots) { slot in
                        cardButton(for: slot)
                    }
                }
                .padding(16)
            }
            // ↑ ↓ Home End step through the cards in reading order (#57).
            .focusable()
            .focusEffectDisabled()
            .keyboardListNavigation(
                ids: { SchedulesMasterTable.keyboardOrder(slots: slots, newDraftSlot: newDraftSlot) },
                selection: $selectedSlotKey
            ) { key in
                proxy.scrollTo(key)
            }
        }
    }

    @ViewBuilder
    private func cardButton(for slot: Slot) -> some View {
        let isSelected = selectedSlotKey == slot.key
        Button {
            selectedSlotKey = slot.key
        } label: {
            SlotCard(slot: slot, isSelected: isSelected, warnings: warnings[slot.key] ?? [])
        }
        .buttonStyle(.plainHit)
        .accessibilitySelected(isSelected)
        .id(slot.key)
    }
}
