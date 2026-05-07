import SwiftUI

/// One row in `SchedulesMasterTable`. Six "columns": NAME / TYPE / TIME /
/// DAYS / ON MISS / COOLDOWN. Selection state is owned by the parent;
/// we just render isSelected styling.
struct SlotTableRow: View {
    let slot: Slot
    let isSelected: Bool

    var body: some View {
        HStack(spacing: 16) {
            nameCell.frame(maxWidth: .infinity, alignment: .leading)
            typeCell.frame(width: 140, alignment: .leading)
            timeCell.frame(width: 70, alignment: .leading)
            daysCell.frame(width: 200, alignment: .leading)
            onMissCell.frame(width: 90, alignment: .leading)
            cooldownCell.frame(width: 90, alignment: .leading)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 16)
        .background(rowBackground)
        .overlay(alignment: .leading) {
            if isSelected {
                Rectangle()
                    .fill(DS.Accent.fill)
                    .frame(width: 2)
            }
        }
    }

    @ViewBuilder
    private var rowBackground: some View {
        if isSelected {
            DS.Paper.raised
        } else {
            Color.clear
        }
    }

    private var nameCell: some View {
        HStack(spacing: 6) {
            Text(slot.key)
                .font(DS.mono(13))
                .foregroundStyle(DS.Ink.p1)
            Image(systemName: "lock.fill")
                .font(.system(size: 10))
                .foregroundStyle(DS.Ink.p4)
        }
    }

    private var typeCell: some View {
        TypePill(type: slot.type)
    }

    private var timeCell: some View {
        Text(slot.firesAtLocal)
            .font(DS.mono(14, weight: .semibold))
            .foregroundStyle(DS.Ink.p1)
    }

    private var daysCell: some View {
        VStack(alignment: .leading, spacing: 4) {
            DayCircleStrip(
                activeDays: Set(slot.weekdays),
                typeColor: DS.SlotType.color(for: slot.type),
                diameter: 16
            )
            Text(WeekdaysFormatter.label(for: slot.weekdays))
                .font(DS.sans(11))
                .foregroundStyle(DS.Ink.p3)
        }
    }

    private var onMissCell: some View {
        OnMissPill(policy: slot.onMiss)
    }

    private var cooldownCell: some View {
        HStack(spacing: 4) {
            Text("\(slot.cooldownMinutes)m")
                .font(DS.mono(13))
                .foregroundStyle(DS.Ink.p2)
            Image(systemName: "bolt.fill")
                .font(.system(size: 8))
                .foregroundStyle(DS.Ink.p4)
        }
    }
}
