import SwiftUI

struct ActionItemsFilter: Equatable {
    enum Status: String, CaseIterable, Identifiable {
        case all, open, done, snoozed
        var id: String { rawValue }
    }
    var kinds: Set<ActionSection.Kind>   // empty = all
    var status: Status
    var searchText: String
}

/// Editorial filter bar. Left: segmented All/Open/Done/Snoozed.
/// Right: flat chip filters for section kinds, each with a priority dot
/// and a small count badge.
struct FilterChipsView: View {
    @Binding var filter: ActionItemsFilter

    private static let kindOptions: [(kind: ActionSection.Kind, label: String)] = [
        (.urgent,   "Urgent"),
        (.todo,     "To Do"),
        (.watching, "Watching"),
        (.personal, "Personal"),
        (.focus,    "Focus"),
        (.meetings, "Meetings"),
        (.done,     "Done"),
        (.digest,   "Digest"),
    ]

    var body: some View {
        HStack(spacing: 4) {
            statusSegment
                .padding(.trailing, 8)

            allChip
            ForEach(Self.kindOptions, id: \.kind) { opt in
                kindChip(opt.kind, label: opt.label)
            }
            Spacer(minLength: 0)
        }
    }

    // MARK: - Segmented status

    private var statusSegment: some View {
        HStack(spacing: 2) {
            ForEach(ActionItemsFilter.Status.allCases) { s in
                segmentButton(s)
            }
        }
        .padding(2)
        .background(
            RoundedRectangle(cornerRadius: 7)
                .fill(DS.Paper.sunk.opacity(0.8))
                .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
    }

    private func segmentButton(_ s: ActionItemsFilter.Status) -> some View {
        let on = filter.status == s
        return Button {
            filter.status = s
        } label: {
            Text(s.rawValue.capitalized)
                .font(DS.sans(12, weight: .medium))
                .foregroundStyle(on ? DS.Ink.p1 : DS.Ink.p2)
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
                .frame(height: 22)
                .background {
                    if on {
                        RoundedRectangle(cornerRadius: 5).fill(DS.Paper.raised)
                            .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                    }
                }
        }
        .buttonStyle(.plainHit)
    }

    // MARK: - Chips

    private var allChip: some View {
        chipButton(
            label: "All",
            dot: nil,
            count: nil,
            selected: filter.kinds.isEmpty,
            action: { filter.kinds = [] }
        )
        .help("Show tasks from every section")
    }

    private func kindChip(_ kind: ActionSection.Kind, label: String) -> some View {
        let selected = filter.kinds.contains(kind)
        return chipButton(
            label: label,
            dot: DS.priorityColor(kind),
            count: nil,
            selected: selected,
            action: { toggle(kind) }
        )
        .contextMenu {
            Button("Only \(label)") { filter.kinds = [kind] }
            Button("Show all") { filter.kinds = [] }
        }
        .help(selected ? "Hide \(label) (right-click for solo)" : "Show \(label) (right-click for solo)")
    }

    @ViewBuilder
    private func chipButton(
        label: String,
        dot: Color?,
        count: Int?,
        selected: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let dot {
                    Circle()
                        .fill(dot)
                        .frame(width: 8, height: 8)
                        .overlay(
                            Circle().strokeBorder(DS.Paper.base.opacity(0.8), lineWidth: 2)
                                .frame(width: 12, height: 12)
                        )
                        .frame(width: 8, height: 8)
                }
                Text(label)
                    .font(DS.sans(12, weight: .medium))
                if let count {
                    Text("\(count)")
                        .font(DS.mono(10.5, weight: .medium))
                        .foregroundStyle(DS.Ink.p4)
                }
            }
            .foregroundStyle(selected ? DS.Ink.p1 : DS.Ink.p3)
            .padding(.horizontal, 10)
            .frame(height: 26)
            .background {
                if selected {
                    // Selected chip uses a recessed paper fill — visually maps to the
                    // neumorphic-pressed state from the design tokens.
                    RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk)
                        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                }
            }
        }
        .buttonStyle(.plainHit)
    }

    // MARK: - Toggle logic

    /// Click behavior:
    /// - From "all" (empty set) → solo the clicked kind
    /// - Currently selected → deselect it; if that empties the set, snap back to "all"
    /// - Currently unselected → add to the current selection
    private func toggle(_ kind: ActionSection.Kind) {
        if filter.kinds.isEmpty {
            filter.kinds = [kind]
        } else if filter.kinds.contains(kind) {
            filter.kinds.remove(kind)
        } else {
            filter.kinds.insert(kind)
        }
    }
}
