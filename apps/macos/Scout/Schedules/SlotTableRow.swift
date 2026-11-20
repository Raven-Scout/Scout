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
    /// Run-health warnings about this schedule, shown on a line under the row.
    var warnings: [RunOutcomeDisplay.SlotWarning] = []
    /// The table's column widths at the current Text size (UI audit T4).
    var columns = SchedulesColumns()
    /// `.dots` drops the "weekdays" word beside the day dots; it stays the tooltip.
    var mode: SchedulesColumns.Mode = .full

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: SchedulesColumns.spacing) {
                nameCell.frame(width: nameWidth, alignment: .leading)
                typeCell.frame(width: columns.type, alignment: .leading)
                timeCell.frame(width: columns.time, alignment: .leading)
                daysCell.frame(width: columns.days(mode), alignment: .leading)
                onMissCell.frame(width: columns.onMiss, alignment: .leading)
                cooldownCell.frame(width: columns.cooldown, alignment: .leading)
            }
            SlotWarningLines(warnings: warnings)
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
            Text(slot.displayName)
                .font(DS.font(.body))
                .foregroundStyle(DS.Ink.p1)
                .lineLimit(1)
                .truncationMode(.tail)
                .help(slot.key)
            if let model = slot.model {
                SlotModelTag(model: model)
            }
        }
    }

    private var typeCell: some View {
        TypePill(type: slot.type)
    }

    private var timeCell: some View {
        Text(slot.firesAtLocal)
            .font(DS.font(.headline, .mono, weight: .semibold))
            .foregroundStyle(DS.Ink.p1)
    }

    private var daysCell: some View {
        let label = WeekdaysFormatter.label(for: slot.weekdays)
        return HStack(spacing: SchedulesColumns.dotsGap) {
            DayCircleStrip(
                activeDays: Set(slot.weekdays),
                typeColor: DS.SlotType.color(for: slot.type),
                diameter: 16
            )
            if mode == .full {
                // One line: "weekdays" wrapped to "weekda / ys" at Largest text (T4).
                Text(label)
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(1)
            }
        }
        .help(label)
    }

    private var onMissCell: some View {
        OnMissPill(policy: slot.onMiss)
    }

    private var cooldownCell: some View {
        HStack(spacing: 4) {
            Text("\(slot.cooldownMinutes)m")
                .font(DS.font(.body, .mono))
                .foregroundStyle(DS.Ink.p2)
                .lineLimit(1)
            Image(systemName: "bolt.fill")
                .font(DS.font(.micro))
                .foregroundStyle(DS.Ink.p4)
        }
    }
}
