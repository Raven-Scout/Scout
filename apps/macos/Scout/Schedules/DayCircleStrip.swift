import SwiftUI

/// 7-circle weekday strip: M T W T F S S. Each circle is filled in the
/// slot's type color when the day is active; otherwise drawn as an outline
/// in DS.Ink.p4. Used by SlotTableRow (16pt) and SlotCard (12pt).
struct DayCircleStrip: View {
    let activeDays: Set<String>
    let typeColor: Color
    let diameter: CGFloat

    private static let order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    var body: some View {
        HStack(spacing: 4) {
            ForEach(Self.order, id: \.self) { day in
                circle(for: day)
                    .accessibilityLabel(day)
                    .accessibilityValue(activeDays.contains(day) ? "active" : "inactive")
            }
        }
    }

    @ViewBuilder
    private func circle(for day: String) -> some View {
        let active = activeDays.contains(day)
        ZStack {
            if active {
                Circle().fill(typeColor)
            } else {
                Circle().stroke(DS.Ink.p4, lineWidth: 1)
            }
            Text(letterFor(day))
                .font(DS.sans(max(8, diameter * 0.55), weight: .medium))
                .foregroundStyle(active ? DS.Paper.base : DS.Ink.p3)
        }
        .frame(width: diameter, height: diameter)
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
