import SwiftUI

/// Filter chips row above the master list. `All` + per-type chips with
/// derived counts. Single-select; clicking a type chip swaps selection.
/// Types with zero slots in the source list have their chip hidden.
struct SchedulesFilterChips: View {
    @Binding var filterMode: SchedulesFilterMode
    let slots: [Slot]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                allChip
                ForEach(SlotType.allCases, id: \.self) { type in
                    if SchedulesFilterMode.count(of: type, in: slots) > 0 {
                        typeChip(for: type)
                    }
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 8)
        }
    }

    private var allChip: some View {
        chip(
            label: "All",
            count: slots.count,
            isSelected: filterMode == .all,
            dotColor: nil
        ) {
            filterMode = .all
        }
    }

    private func typeChip(for type: SlotType) -> some View {
        chip(
            label: type.rawValue.capitalized,
            count: SchedulesFilterMode.count(of: type, in: slots),
            isSelected: filterMode == .type(type),
            dotColor: DS.SlotType.color(for: type)
        ) {
            filterMode = .type(type)
        }
    }

    @ViewBuilder
    private func chip(
        label: String,
        count: Int,
        isSelected: Bool,
        dotColor: Color?,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let dotColor {
                    Circle()
                        .fill(dotColor)
                        .frame(width: 6, height: 6)
                }
                Text(label)
                    .font(DS.sans(12, weight: .medium))
                Text("\(count)")
                    .font(DS.mono(11))
                    .foregroundStyle(isSelected ? DS.Paper.base.opacity(0.85) : DS.Ink.p3)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(
                Capsule().fill(isSelected ? DS.Ink.p1 : DS.Paper.raised)
            )
            .foregroundStyle(isSelected ? DS.Paper.base : DS.Ink.p2)
        }
        .buttonStyle(.plain)
    }
}
