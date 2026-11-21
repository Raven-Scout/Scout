import SwiftUI

/// One slot rendered as a card in `SchedulesMasterCards`. A left edge in the
/// slot type color; serif time at top; type pill, slot key, day strip,
/// cooldown, and on-miss policy below.
struct SlotCard: View {
    let slot: Slot
    let isSelected: Bool
    /// Run-health warnings about this schedule, shown at the foot of the card.
    var warnings: [RunOutcomeDisplay.SlotWarning] = []
    /// The table's 24-hour "08:05", for a table stacked into cards at large text
    /// (UI audit T4), so the times don't change form with the layout.
    var twentyFourHour = false

    private var typeColor: Color { DS.SlotType.color(for: slot.type) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            topRow
            HStack(spacing: 6) {
                Text(slot.displayName)
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Ink.p2)
                    .help(slot.key)
                if let model = slot.model {
                    SlotModelTag(model: model)
                }
            }
            DayCircleStrip(
                activeDays: Set(slot.weekdays),
                typeColor: typeColor,
                diameter: 12
            )
            cooldownRow
            OnMissPill(policy: slot.onMiss)
            SlotWarningLines(warnings: warnings)
        }
        .padding(.horizontal, Self.horizontalPadding)
        .padding(.vertical, 14)
        .editorialCard(padding: 0, cornerRadius: 10, selected: isSelected, accentBar: typeColor)
    }

    /// The time with the type pill beside it, or under it when a narrow card at
    /// a large Text size can't fit both on a line (UI audit T4).
    private var topRow: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                time
                Spacer(minLength: 8)
                TypePill(type: slot.type)
            }
            VStack(alignment: .leading, spacing: 6) {
                time
                TypePill(type: slot.type)
            }
        }
    }

    private var time: some View { Self.time(slot.firesAtLocal, twentyFourHour: twentyFourHour) }

    /// The big serif time, with AM/PM unless 24-hour. One line, at its own width.
    static func time(_ firesAtLocal: String, twentyFourHour: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(twentyFourHour ? firesAtLocal : formattedTime(firesAtLocal))
                .dsFont(.display, .serif, weight: .medium)
                .foregroundStyle(DS.Ink.p1)
                .lineLimit(1)
            if !twentyFourHour {
                Text(amPm(firesAtLocal))
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(1)
            }
        }
        .fixedSize()
    }

    /// The card's padding either side (`body`).
    static let horizontalPadding: CGFloat = 16

    private var cooldownRow: some View {
        HStack(spacing: 6) {
            Text("COOLDOWN")
                .font(DS.font(.caption2, weight: .medium))
                .tracking(1)
                .foregroundStyle(DS.Ink.p4)
            Text("\(slot.cooldownMinutes)m")
                .font(DS.font(.label, .mono))
                .foregroundStyle(DS.Ink.p2)
        }
    }

    /// Convert "HH:MM" 24-hour to "H:MM" 12-hour for the big card display.
    nonisolated static func formattedTime(_ firesAtLocal: String) -> String {
        let parts = firesAtLocal.split(separator: ":")
        guard parts.count == 2,
              let h24 = Int(parts[0]), let m = Int(parts[1])
        else { return firesAtLocal }
        let h12 = h24 == 0 ? 12 : (h24 > 12 ? h24 - 12 : h24)
        return String(format: "%d:%02d", h12, m)
    }

    nonisolated static func amPm(_ firesAtLocal: String) -> String {
        let parts = firesAtLocal.split(separator: ":")
        guard let h24 = parts.first.flatMap({ Int($0) }) else { return "" }
        return h24 < 12 ? "AM" : "PM"
    }
}
