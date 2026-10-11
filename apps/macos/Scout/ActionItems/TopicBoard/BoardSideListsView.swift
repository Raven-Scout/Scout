import SwiftUI

/// The lists under the topics: what got done, for this week, today or yesterday.
struct BoardSideListsView: View {
    let done: [DoneEntry]
    @Binding var doneRange: DoneRange

    var body: some View {
        HStack(alignment: .top, spacing: 20) {
            SideListView(isEmpty: done.isEmpty) {
                HStack(spacing: 4) {
                    Text("Done")
                    Button {
                        doneRange = doneRange.next
                    } label: {
                        Text(doneRange.label)
                            .underline(pattern: .dot)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(DS.Ink.p2)
                    .help("Switch between this week, today and yesterday")
                }
            } rows: {
                ForEach(done) { entry in
                    VStack(alignment: .leading, spacing: 1) {
                        Text([entry.task.shortPrefix, entry.date.formatted(.dateTime.day().month(.abbreviated))]
                            .compactMap { $0 }
                            .joined(separator: " · "))
                            .font(DS.sans(11.5))
                            .foregroundStyle(DS.Ink.p3)
                        Text(entry.task.plainSubject)
                            .font(DS.sans(13))
                            .foregroundStyle(DS.Ink.p2)
                    }
                }
            }
        }
    }
}
