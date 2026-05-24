import SwiftUI

struct DatePickerToolbarItem: View {
    @Binding var date: Date
    let todayET: Date

    @State private var calendarPopoverOpen = false

    var body: some View {
        HStack(spacing: 6) {
            Button {
                if let d = Calendar(identifier: .iso8601).date(byAdding: .day, value: -1, to: date) {
                    date = d
                }
            } label: { Image(systemName: "chevron.left") }

            Button { calendarPopoverOpen.toggle() } label: {
                Text(formattedDate).monospacedDigit()
            }
            .popover(isPresented: $calendarPopoverOpen, arrowEdge: .bottom) {
                DatePicker("", selection: $date, displayedComponents: .date)
                    .datePickerStyle(.graphical)
                    .labelsHidden()
                    .padding(12)
            }

            Button {
                if let d = Calendar(identifier: .iso8601).date(byAdding: .day, value: 1, to: date) {
                    date = d
                }
            } label: { Image(systemName: "chevron.right") }

            if !Calendar.current.isDate(date, inSameDayAs: todayET) {
                Button("Today") { date = todayET }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
        }
    }

    private var formattedDate: String {
        let fmt = DateFormatter()
        fmt.dateFormat = "d. M. yyyy"
        return fmt.string(from: date)
    }
}
