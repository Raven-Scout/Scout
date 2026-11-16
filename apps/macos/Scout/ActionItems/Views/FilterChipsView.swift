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
/// Right: flat chip filters for section kinds, each with a kind marker
/// and a small count badge.
///
/// The full row (status segments plus nine chips) needs about 1,000 pt, more
/// than the page gets below a ~1,500 pt window (UI audit C6: at 1280 pt it
/// ran off the left edge, at 1100 pt it clipped on both sides). So it steps
/// down: first the chips fold into a "Sections" menu, then the status
/// segments fold into a "Show" menu too. Whatever is chosen stays visible in
/// the menu labels.
struct FilterChipsView: View {
    @Binding var filter: ActionItemsFilter

    static let kindOptions: [(kind: ActionSection.Kind, label: String)] = [
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
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 4) {
                statusSegment
                    .padding(.trailing, 8)
                allChip
                ForEach(Self.kindOptions, id: \.kind) { opt in
                    kindChip(opt.kind, label: opt.label)
                }
            }
            HStack(spacing: 8) {
                statusSegment
                kindMenu
            }
            HStack(spacing: 8) {
                statusMenu
                kindMenu
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Compact menus

    /// "Sections: All", "Sections: Urgent", "Sections: Urgent + 2".
    static func kindMenuTitle(for kinds: Set<ActionSection.Kind>) -> String {
        let chosen = kindOptions.filter { kinds.contains($0.kind) }.map(\.label)
        switch chosen.count {
        case 0:  return "Sections: All"
        case 1:  return "Sections: \(chosen[0])"
        default: return "Sections: \(chosen[0]) + \(chosen.count - 1)"
        }
    }

    static func statusTitle(_ status: ActionItemsFilter.Status) -> String {
        status.rawValue.capitalized
    }

    private var kindMenu: some View {
        Menu {
            Button("All sections") { filter.kinds = [] }
            Divider()
            ForEach(Self.kindOptions, id: \.kind) { opt in
                Toggle(opt.label, isOn: Binding(
                    get: { filter.kinds.contains(opt.kind) },
                    set: { _ in toggle(opt.kind) }
                ))
            }
        } label: {
            Text(Self.kindMenuTitle(for: filter.kinds))
                .font(DS.sans(12, weight: .medium))
                .lineLimit(1)
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help("Choose which sections to show")
        .accessibilityLabel("Sections")
        .accessibilityValue(Self.kindMenuTitle(for: filter.kinds))
    }

    private var statusMenu: some View {
        Menu {
            Picker("Show", selection: $filter.status) {
                ForEach(ActionItemsFilter.Status.allCases) { Text(Self.statusTitle($0)).tag($0) }
            }
            .pickerStyle(.inline)
        } label: {
            Text("Show: \(Self.statusTitle(filter.status))")
                .font(DS.sans(12, weight: .medium))
                .lineLimit(1)
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help("Show all, open, done or snoozed action items")
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
            Text(Self.statusTitle(s))
                .font(DS.sans(12, weight: .medium))
                .lineLimit(1)
                .fixedSize()
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
            kind: nil,
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
            kind: kind,
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
        kind: ActionSection.Kind?,
        count: Int?,
        selected: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let kind {
                    KindMarker(kind: kind, size: 12)
                }
                Text(label)
                    .font(DS.sans(12, weight: .medium))
                    .lineLimit(1)
                    .fixedSize()
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
