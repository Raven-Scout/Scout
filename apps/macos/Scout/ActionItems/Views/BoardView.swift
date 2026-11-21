import SwiftUI

/// Read-only status board for Action Items (issue #15). Renders the day's
/// sections as columns (Urgent / To Do / Watching / [Personal] / Done) with the
/// existing status vocabulary mapped 1:1 to columns. The Done column is
/// collapsed by default. No drag-and-drop — acting on a task happens in the
/// List view, which sidesteps the markdown-as-source mutation concerns in #10.
struct BoardView: View {
    let columns: [ActionBoardColumn]
    let scoutDirectory: URL
    let density: ActionItemsDisplay.Density
    let fields: ActionItemsDisplay.Fields
    /// Switches the page to the List view, offered by the read-only hint.
    /// `nil` leaves the button out.
    var openList: (() -> Void)?

    /// The hint above the columns (audit #49): the board looked editable but
    /// nothing on it acts on a task.
    static let readOnlyHint = "The board is an overview. To mark tasks done, snooze them or add comments, use the List."

    /// Done starts collapsed; toggled per session.
    @State private var doneCollapsed = true
    /// Rows built per column — the board lays cards out eagerly too, so a
    /// column holding the whole backlog froze it the same way (see `TaskWindow`).
    @State private var windows: [ActionSection.Kind: TaskWindow] = [:]

    /// Columns already arranged by the caller. `ActionItemsView` sorts and
    /// caches them, so the board does no per-body work.
    init(
        columns: [ActionBoardColumn],
        scoutDirectory: URL,
        density: ActionItemsDisplay.Density = .comfortable,
        fields: ActionItemsDisplay.Fields = .init(),
        openList: (() -> Void)? = nil
    ) {
        self.columns = columns
        self.scoutDirectory = scoutDirectory
        self.density = density
        self.fields = fields
        self.openList = openList
    }

    /// Columns built from the sections in file order.
    init(
        sections: [ActionSection],
        scoutDirectory: URL,
        density: ActionItemsDisplay.Density = .comfortable,
        fields: ActionItemsDisplay.Fields = .init()
    ) {
        self.init(columns: ActionBoardColumn.columns(from: sections), scoutDirectory: scoutDirectory,
                  density: density, fields: fields)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            readOnlyBar
            ScrollView([.horizontal, .vertical]) {
                HStack(alignment: .top, spacing: 16) {
                    ForEach(columns) { column in
                        columnView(column)
                    }
                }
                .padding(.horizontal, 28)
                .padding(.top, 14)
                .padding(.bottom, 24)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var readOnlyBar: some View {
        HStack(spacing: 8) {
            Image(systemName: "eye")
                .foregroundStyle(DS.Ink.p4)
                .accessibilityHidden(true)
            Text(Self.readOnlyHint)
                .foregroundStyle(DS.Ink.p3)
                .fixedSize(horizontal: false, vertical: true)
            if let openList {
                Button("Open List", action: openList)
                    .buttonStyle(.ds(.link, size: .small))
            }
            Spacer(minLength: 0)
        }
        .font(DS.font(.caption))
        .padding(.horizontal, 28)
        .padding(.top, 14)
        .accessibilityElement(children: .contain)
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
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.vertical, 8)
            } else {
                let window = windows[column.kind] ?? TaskWindow()
                let showMore = { windows[column.kind, default: TaskWindow()].showMore() }
                let showAll = { windows[column.kind, default: TaskWindow()].showAll(in: column.tasks) }
                WindowSummaryRow(window: window, tasks: column.tasks, action: showMore, showAll: showAll)
                ForEach(window.visible(column.tasks)) { task in
                    BoardCardView(task: task, kind: column.kind, scoutDirectory: scoutDirectory,
                                  density: density, fields: fields)
                }
                ShowMoreRow(window: window, tasks: column.tasks, action: showMore, showAll: showAll)
            }
        }
        .frame(width: collapsed ? 200 : 280, alignment: .leading)
    }

    @ViewBuilder
    private func header(_ column: ActionBoardColumn, collapsed: Bool) -> some View {
        let row = HStack(spacing: 8) {
            KindMarker(kind: column.kind, size: 14)
            Text(column.title)
                .font(DS.font(.label, weight: .medium))
                .tracking(0.4)
                .foregroundStyle(DS.Ink.p2)
            Text("\(column.count)")
                .font(DS.font(.caption, .mono, weight: .medium))
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
                .dsReportsOwnFocus()
        } else {
            row
        }
    }
}
