import SwiftUI

/// The table (spec §6.3): every column sortable, severity then recency by
/// default. Shares filters and selection with the board, and is the page's
/// VoiceOver path — a row reads as cells, not as a drawing.
struct SessionsTableView: View {
    let rows: [SessionTableRow]
    let now: Date
    @Binding var selectedID: String?

    @State private var sortOrder: [KeyPathComparator<SessionTableRow>] = [
        KeyPathComparator(\.severity),
        KeyPathComparator(\.lastActivity, order: .reverse),
    ]

    var body: some View {
        Table(rows.sorted(using: sortOrder), selection: $selectedID, sortOrder: $sortOrder) {
            TableColumn("State", value: \.severity) { row in
                HStack(spacing: 6) {
                    SessionStateDot(state: row.session.state, isOpen: row.session.isOpen)
                    Text(row.session.state.label).font(DS.sans(11.5)).foregroundStyle(DS.Ink.p3)
                }
            }
            .width(min: 80, ideal: 92)
            TableColumn("Title", value: \.title) { row in
                Text(row.title).font(DS.sans(12.5)).foregroundStyle(DS.Ink.p1).lineLimit(1)
            }
            .width(min: 160, ideal: 260)
            TableColumn("Project", value: \.projectName)
                .width(min: 80, ideal: 120)
            TableColumn("Reason", value: \.reason)
                .width(min: 120, ideal: 200)
            TableColumn("PR", value: \.prNumber) { row in
                Text(row.session.pr.map(SessionsFormat.prChip) ?? "").font(DS.mono(11))
            }
            .width(min: 60, ideal: 150)
            TableColumn("Last active", value: \.lastActivity) { row in
                Text(SessionsFormat.ago(row.session.lastActivityAt, now: now)).font(DS.sans(11.5))
            }
            .width(min: 70, ideal: 80)
            TableColumn("Model", value: \.model) { row in
                Text(row.session.model.map(SessionsFormat.shortModel) ?? "").font(DS.mono(11))
            }
            .width(min: 60, ideal: 80)
            TableColumn("Turns", value: \.turns) { row in
                Text(row.session.turns.map(String.init) ?? "").font(DS.mono(11))
            }
            .width(min: 40, ideal: 50)
        }
    }
}
