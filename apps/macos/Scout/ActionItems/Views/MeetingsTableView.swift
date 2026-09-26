import SwiftUI

/// Editorial meetings table. Mirrors the handoff bundle:
/// header row in sans-smallcaps, time column in mono, tag column right-aligned.
struct MeetingsTableView: View {
    /// The tables to draw. Takes the array rather than the whole section so a
    /// collapsed `<details>` group can reuse the same renderer for its
    /// archived tables.
    let tables: [ActionSection.Table]

    var body: some View {
        ForEach(Array(tables.enumerated()), id: \.offset) { _, table in
            tableView(table)
        }
    }

    @ViewBuilder
    private func tableView(_ table: ActionSection.Table) -> some View {
        VStack(spacing: 0) {
            HStack(alignment: .center, spacing: 0) {
                ForEach(Array(table.headers.enumerated()), id: \.offset) { idx, h in
                    Text(h.uppercased())
                        .font(DS.sans(11, weight: .medium))
                        .tracking(0.05 * 11)
                        .foregroundStyle(DS.Ink.p4)
                        .frame(maxWidth: .infinity, alignment: idx == table.headers.count - 1 ? .trailing : .leading)
                }
            }
            .padding(.vertical, 10)
            .overlay(alignment: .bottom) { EditorialRule() }

            ForEach(Array(table.rows.enumerated()), id: \.offset) { _, row in
                HStack(alignment: .top, spacing: 0) {
                    ForEach(Array(row.enumerated()), id: \.offset) { idx, cell in
                        cellView(cell, isTime: idx == 0, isTag: idx == row.count - 1)
                            .frame(maxWidth: .infinity, alignment: idx == row.count - 1 ? .trailing : .leading)
                    }
                }
                .padding(.vertical, 10)
                .overlay(alignment: .bottom) { EditorialRule() }
            }
        }
    }

    @ViewBuilder
    private func cellView(_ raw: String, isTime: Bool, isTag: Bool) -> some View {
        if isTime {
            Text(raw)
                .font(DS.mono(12.5, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
        } else if isTag {
            Text(raw)
                .font(DS.mono(11, weight: .medium))
                .foregroundStyle(DS.Ink.p3)
                .padding(.horizontal, 6)
                .padding(.vertical, 3)
                .background(EditorialChipBackground())
        } else {
            InlineMarkdownText(raw)
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p1)
        }
    }
}
