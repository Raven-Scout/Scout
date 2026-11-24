import SwiftUI

/// One change in the list: what it did, why (in plain words), how its test reads, and a small
/// before/after sparkline of the engagement around it. Pressing it opens the change's detail.
struct ChangeCard: View {
    let row: ChangeRow
    let days: [EngagementDay]
    var calendar: Calendar = .current
    let onOpen: () -> Void

    /// Newest first; ties keep the engine's order.
    static func newestFirst(_ rows: [ChangeRow]) -> [ChangeRow] {
        rows.enumerated().sorted { a, b in
            a.element.ts != b.element.ts ? a.element.ts > b.element.ts : a.offset < b.offset
        }.map(\.element)
    }

    /// The icon for a kind of change; anything unknown gets a plain pencil.
    static func symbol(forKind kind: String) -> String {
        switch kind {
        case "brain-edit": return "brain"
        case "driver": return "gearshape"
        case "kb-structure": return "folder"
        case "phase": return "list.bullet.rectangle"
        case "rollback": return "arrow.uturn.backward"
        case "upgrade": return "arrow.up.circle"
        case "wishlist-done": return "checkmark.seal"
        default: return "square.and.pencil"
        }
    }

    var body: some View {
        let spark = DetailModel.sparkline(days: days, around: row.ts, calendar: calendar)
        Button(action: onOpen) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: Self.symbol(forKind: row.kind))
                    .foregroundStyle(DS.Ink.p3)
                    .frame(width: 20)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(verbatim: row.summary)
                        .font(DS.font(.label, weight: .semibold))
                        .foregroundStyle(DS.Ink.p1)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(verbatim: DetailModel.gloss(row.why))
                        .font(DS.font(.caption))
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(row.ts.formatted(.dateTime.month(.abbreviated).day()))
                        .font(DS.font(.caption2))
                        .foregroundStyle(DS.Ink.p4)
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 6) {
                    VerdictPill(test: row.test, inferred: row.why.inferred)
                    sparkline(spark)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .editorialCard()
        .accessibilityElement(children: .combine)
        .accessibilityLabel(Self.spokenLabel(row))
        .accessibilityHint("Opens the details")
    }

    /// What a screen reader says: the summary, the reason and the verdict. Never an id.
    static func spokenLabel(_ row: ChangeRow) -> String {
        "\(row.summary). \(DetailModel.gloss(row.why)). \(VerdictPill.spokenLabel(row.test, inferred: row.why.inferred))"
    }

    @ViewBuilder
    private func sparkline(_ spark: (before: [Int], after: [Int])) -> some View {
        if spark.before.isEmpty && spark.after.isEmpty {
            EmptyView()
        } else {
            HStack(spacing: 4) {
                Text(DigestCard.sparkline(spark.before)).foregroundStyle(DS.Ink.p4)
                Rectangle().fill(DS.Rule.soft).frame(width: 1, height: 10)
                Text(DigestCard.sparkline(spark.after)).foregroundStyle(DS.Ink.p2)
            }
            .font(.system(.caption2, design: .monospaced))
            .accessibilityHidden(true)
            .help("Engagement over the 14 days before and after this change")
        }
    }
}
