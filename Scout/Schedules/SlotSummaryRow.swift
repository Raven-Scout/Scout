import SwiftUI

/// One-line summary view for a slot in the collapsed state.
/// Format: `<slot-key> · <type> · <HH:MM> <weekday-shortlist>`
/// Examples:
///   morning-briefing · briefing · 08:00 MTWThF
///   weekend-briefing · briefing · 08:30 SaSu
struct SlotSummaryRow: View {
    let slot: Slot
    let hasDirtyDraft: Bool
    let isExpanded: Bool

    var summary: String {
        "\(slot.key) · \(slot.type.rawValue) · \(slot.firesAtLocal) \(weekdaysShort)"
    }

    private var weekdaysShort: String {
        // Map full weekday names to one- or two-char abbreviations:
        // Mon→M, Tue→T, Wed→W, Thu→Th, Fri→F, Sat→Sa, Sun→Su.
        slot.weekdays.map {
            switch $0 {
            case "Mon": return "M"
            case "Tue": return "T"
            case "Wed": return "W"
            case "Thu": return "Th"
            case "Fri": return "F"
            case "Sat": return "Sa"
            case "Sun": return "Su"
            default:    return $0
            }
        }.joined()
    }

    var body: some View {
        HStack {
            Text(slot.key).font(.body.monospaced())
            Text("·").foregroundStyle(.secondary)
            Text(slot.type.rawValue).foregroundStyle(.secondary)
            Text("·").foregroundStyle(.secondary)
            Text(slot.firesAtLocal).foregroundStyle(.secondary)
            Text(weekdaysShort).foregroundStyle(.secondary)
            if hasDirtyDraft {
                Image(systemName: "circle.fill").foregroundStyle(.orange).font(.system(size: 6))
            }
            Spacer()
            Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())  // make whole row tappable
    }
}
