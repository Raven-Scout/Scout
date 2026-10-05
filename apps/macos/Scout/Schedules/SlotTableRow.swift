import SwiftUI

/// One row in `SchedulesMasterTable`. Six "columns": NAME / TYPE / TIME /
/// DAYS / ON MISS / COOLDOWN. Selection state is owned by the parent;
/// we just render isSelected styling.
struct SlotTableRow: View {
    let slot: Slot
    let isSelected: Bool
    /// Width of the flexible NAME column, computed by `SchedulesMasterTable`
    /// from the available pane width so rows align with the header.
    let nameWidth: CGFloat

    var body: some View {
        HStack(spacing: SchedulesColumns.spacing) {
            nameCell.frame(width: nameWidth, alignment: .leading)
            typeCell.frame(width: SchedulesColumns.type, alignment: .leading)
            timeCell.frame(width: SchedulesColumns.time, alignment: .leading)
            daysCell.frame(width: SchedulesColumns.days, alignment: .leading)
            onMissCell.frame(width: SchedulesColumns.onMiss, alignment: .leading)
            cooldownCell.frame(width: SchedulesColumns.cooldown, alignment: .leading)
        }
        .padding(.vertical, 6)
        .padding(.horizontal, SchedulesColumns.hPadding)
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
                .lineLimit(1)
                .truncationMode(.tail)
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
        HStack(spacing: 10) {
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
