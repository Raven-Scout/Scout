import SwiftUI

/// Inbox, Loose ends and Done, side by side under the topics.
struct BoardSideListsView: View {
    let inbox: [SideEntry]
    let looseEnds: [SideEntry]
    let done: [DoneEntry]
    @Binding var doneRange: DoneRange
    /// `nil` hides the × (the engine can't record a dismissal).
    let onDismiss: ((SideEntry) -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: 20) {
            entries("Inbox", inbox)
            entries("Loose ends", looseEnds)
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

    private func entries(_ title: String, _ rows: [SideEntry]) -> some View {
        SideListView(isEmpty: rows.isEmpty) {
            Text(title)
        } rows: {
            ForEach(rows) { entry in
                SideEntryRow(entry: entry, onClose: onDismiss.map { dismiss in { dismiss(entry) } })
            }
        }
    }
}
