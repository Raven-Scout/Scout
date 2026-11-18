import SwiftUI

/// 7-circle weekday strip: M T W T F S S. Each circle is filled in the
/// slot's type color when the day is active; otherwise drawn as an outline
/// in DS.Ink.decorative. Used by SlotTableRow (16pt) and SlotCard (12pt).
struct DayCircleStrip: View {
    let activeDays: Set<String>
    let typeColor: Color
    let diameter: CGFloat

    private static let order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    var body: some View {
        HStack(spacing: 4) {
            ForEach(Self.order, id: \.self) { day in
                circle(for: day)
            }
        }
        // One element that names the days, not seven "Mon, active" stops.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(SchedulesTimelineView.weekdaysAccessibilityLabel(Array(activeDays)))
    }

    @ViewBuilder
    private func circle(for day: String) -> some View {
        let active = activeDays.contains(day)
        ZStack {
            if active {
                Circle().fill(typeColor)
            } else {
                Circle().stroke(DS.Ink.decorative, lineWidth: 1)
            }
            Text(letterFor(day))
                .font(DS.sans(max(8, diameter * 0.55), weight: .medium))
                // Paper on the slot hue was 2.1–3.7:1 in light mode.
                .foregroundStyle(active ? DS.Ink.onTint : DS.Ink.p3)
        }
        // Grows with the text size so the letter doesn't clip.
        .frame(width: DS.scaled(diameter), height: DS.scaled(diameter))
    }

    private func letterFor(_ day: String) -> String {
        switch day {
        case "Mon": return "M"
        case "Tue": return "T"
        case "Wed": return "W"
        case "Thu": return "T"
        case "Fri": return "F"
        case "Sat": return "S"
        case "Sun": return "S"
        default:    return String(day.prefix(1))
        }
    }
}
