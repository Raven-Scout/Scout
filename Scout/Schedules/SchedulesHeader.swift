import Combine
import SwiftUI

/// Header bar for the Schedules tab. Serif title, live subtitle (count
/// active · type count · current time), view toggle (Table/Cards/Timeline),
/// and the orange + New button.
struct SchedulesHeader: View {
    let slotCount: Int
    let typeCount: Int
    @Binding var viewMode: SchedulesViewMode
    let onAddSlot: () -> Void

    @State private var now: Date = Date()
    private let timer = Timer.publish(every: 60, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Schedules")
                    .font(DS.serif(28, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Text(subtitle)
                    .font(DS.sans(12))
                    .foregroundStyle(DS.Ink.p3)
            }
            Spacer()
            viewToggle
            addNewButton
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
        .onReceive(timer) { now = $0 }
    }

    private var subtitle: String {
        "\(slotCount) active · \(typeCount) types · now \(timeString)"
    }

    private var timeString: String {
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm"
        return fmt.string(from: now)
    }

    private var viewToggle: some View {
        Picker("View", selection: $viewMode) {
            ForEach(SchedulesViewMode.allCases) { mode in
                Text(mode.displayName).tag(mode)
            }
        }
        .pickerStyle(.segmented)
        .frame(width: 280)
    }

    private var addNewButton: some View {
        Button(action: onAddSlot) {
            HStack(spacing: 4) {
                Image(systemName: "plus")
                Text("New")
            }
            .font(DS.sans(13, weight: .medium))
            .padding(.horizontal, 14)
            .padding(.vertical, 7)
            .background(DS.Accent.fill, in: RoundedRectangle(cornerRadius: 7))
            .foregroundStyle(DS.Paper.base)
        }
        .buttonStyle(.plain)
    }
}
