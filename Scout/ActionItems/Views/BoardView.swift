import SwiftUI

/// Read-only status board for Action Items (issue #15). Renders the day's
/// sections as columns (Urgent / To Do / Watching / [Personal] / Done) with the
/// existing status vocabulary mapped 1:1 to columns. The Done column is
/// collapsed by default. No drag-and-drop — acting on a task happens in the
/// List view, which sidesteps the markdown-as-source mutation concerns in #10.
struct BoardView: View {
    let sections: [ActionSection]

    /// Done starts collapsed; toggled per session.
    @State private var doneCollapsed = true

    private var columns: [ActionBoardColumn] {
        ActionBoardColumn.columns(from: sections)
    }

    var body: some View {
        ScrollView([.horizontal, .vertical]) {
            HStack(alignment: .top, spacing: 16) {
                ForEach(columns) { column in
                    columnView(column)
                }
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 24)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    @ViewBuilder
    private func columnView(_ column: ActionBoardColumn) -> some View {
        let collapsed = column.kind == .done && doneCollapsed
        VStack(alignment: .leading, spacing: 10) {
            header(column, collapsed: collapsed)
            if collapsed {
                // Collapsed Done: just the header acts as the affordance.
                EmptyView()
            } else if column.tasks.isEmpty {
                Text("Nothing here")
                    .font(DS.sans(12))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.vertical, 8)
            } else {
                ForEach(column.tasks) { task in
                    BoardCardView(task: task, kind: column.kind)
                }
            }
        }
        .frame(width: collapsed ? 200 : 280, alignment: .leading)
    }

    @ViewBuilder
    private func header(_ column: ActionBoardColumn, collapsed: Bool) -> some View {
        let row = HStack(spacing: 8) {
            KindMarker(kind: column.kind, size: 14)
            Text(column.title)
                .font(DS.sans(12, weight: .medium))
                .tracking(0.4)
                .foregroundStyle(DS.Ink.p2)
            Text("\(column.count)")
                .font(DS.mono(11, weight: .medium))
                .foregroundStyle(DS.Ink.p4)
            Spacer(minLength: 0)
            if column.kind == .done {
                Image(systemName: collapsed ? "chevron.right" : "chevron.down")
                    .imageScale(.small)
                    .foregroundStyle(DS.Ink.p4)
            }
        }
        .padding(.bottom, 6)
        .overlay(alignment: .bottom) { EditorialRule() }

        if column.kind == .done {
            Button { doneCollapsed.toggle() } label: { row.contentShape(Rectangle()) }
                .buttonStyle(.plain)
        } else {
            row
        }
    }
}
