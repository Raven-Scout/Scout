import SwiftUI

/// One slot rendered as a card in `SchedulesMasterCards`. 4pt left border
/// in slot type color; serif time at top; type pill, slot key, day strip,
/// cooldown, and on-miss policy below.
struct SlotCard: View {
    let slot: Slot
    let isSelected: Bool

    private var typeColor: Color { DS.SlotType.color(for: slot.type) }

    var body: some View {
        HStack(spacing: 0) {
            Rectangle()
                .fill(typeColor)
                .frame(width: 4)
            VStack(alignment: .leading, spacing: 12) {
                topRow
                Text(slot.key)
                    .font(DS.mono(12))
                    .foregroundStyle(DS.Ink.p2)
                DayCircleStrip(
                    activeDays: Set(slot.weekdays),
                    typeColor: typeColor,
                    diameter: 12
                )
                cooldownRow
                OnMissPill(policy: slot.onMiss)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
        }
        .background(DS.Paper.raised, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(isSelected ? DS.Accent.fill : Color.clear, lineWidth: 2)
        )
    }

    private var topRow: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(formattedTime)
                .font(DS.serif(28, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
            Text(amPm)
                .font(DS.sans(11))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
            TypePill(type: slot.type)
        }
    }

    private var cooldownRow: some View {
        HStack(spacing: 6) {
            Text("COOLDOWN")
                .font(DS.sans(10, weight: .medium))
                .tracking(1)
                .foregroundStyle(DS.Ink.p4)
            Text("\(slot.cooldownMinutes)m")
                .font(DS.mono(12))
                .foregroundStyle(DS.Ink.p2)
        }
    }

    /// Convert "HH:MM" 24-hour to "H:MM" 12-hour for the big card display.
    private var formattedTime: String {
        let parts = slot.firesAtLocal.split(separator: ":")
        guard parts.count == 2,
              let h24 = Int(parts[0]), let m = Int(parts[1])
        else { return slot.firesAtLocal }
        let h12 = h24 == 0 ? 12 : (h24 > 12 ? h24 - 12 : h24)
        return String(format: "%d:%02d", h12, m)
    }

    private var amPm: String {
        let parts = slot.firesAtLocal.split(separator: ":")
        guard let h24 = parts.first.flatMap({ Int($0) }) else { return "" }
        return h24 < 12 ? "AM" : "PM"
    }
}
