import SwiftUI

struct SectionView: View {
    let section: ActionSection
    let displayedDate: Date
    let scoutDirectory: URL
    let selection: Binding<Set<UUID>>?
    let onOp: @MainActor (WriteOp, Int?) async throws -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            switch section.kind {
            case .focus:
                focus
            case .meetings:
                MeetingsTableView(tables: section.tables)
                    .padding(.top, 4)
            case .done:
                completedList
            case .digest:
                DigestView(section: section)
            default:
                // Deliberately VStack, not LazyVStack (#83, second occurrence).
                // #84's four-way matrix cleared this inner stack on a 21-task
                // file, but heavier real content re-enters the same
                // non-convergent height-estimation loop — measured on macOS 26:
                // scrolling a 298-task day wedges the lazy build at 100% CPU,
                // never the VStack build. Sections are bounded and fully
                // materialized by the parser, so laziness buys nothing here.
                // The frame alignment below must stay .topLeading: a plain
                // VStack hugs its widest child, so horizontal alignment is
                // load-bearing where the width-greedy LazyVStack made it moot.
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(section.tasks) { task in
                        TaskCardView(
                            task: task,
                            kind: section.kind,
                            displayedDate: displayedDate,
                            scoutDirectory: scoutDirectory,
                            selection: selection,
                            onOp: onOp
                        )
                    }
                }
                .frame(maxWidth: .infinity, alignment: .topLeading)
                .padding(.top, 12)
            }
            archive
        }
        .padding(.bottom, 12)
    }

    // MARK: - Archive (`<details>` regions)

    /// Collapsed `<details>` regions, rendered the way the markdown means them:
    /// shut by default, one disclosure per region, below the live list. Their
    /// rows are real tasks, so a parked item can still be worked without
    /// leaving the app — it just doesn't compete with today's work for
    /// attention, and it stays out of the section count.
    @ViewBuilder
    private var archive: some View {
        if !section.collapsed.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(section.collapsed) { group in
                    archiveGroup(group)
                }
            }
            .padding(.top, 14)
        }
    }

    private func archiveGroup(_ group: ActionSection.CollapsedGroup) -> some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(group.tasks) { task in
                    TaskCardView(
                        task: task,
                        kind: section.kind,
                        displayedDate: displayedDate,
                        scoutDirectory: scoutDirectory,
                        // Deliberately not selectable. `ActionItemsView`
                        // derives the selectable set from `section.tasks`, so a
                        // checked archive row would be reconciled away on the
                        // next reparse and silently skipped by Copy — and
                        // "Select all" sweeping a 124-row parked block into a
                        // copy isn't what the button means. Every other action
                        // on the card still works.
                        selection: nil,
                        onOp: onOp
                    )
                }
                ForEach(Array(group.bullets.enumerated()), id: \.offset) { _, bullet in
                    InlineMarkdownText(bullet)
                        .font(DS.serif(13))
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 4)
                }
                MeetingsTableView(tables: group.tables)
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .padding(.top, 6)
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                InlineMarkdownText(group.summary.isEmpty ? "Archived" : group.summary)
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Ink.p4)
                    .fixedSize(horizontal: false, vertical: true)
                if !group.tasks.isEmpty {
                    Text("\(group.tasks.count)")
                        .font(DS.mono(11, weight: .medium))
                        .foregroundStyle(DS.Ink.p4)
                }
                Spacer(minLength: 0)
            }
        }
        .padding(.vertical, 4)
    }

    // MARK: - Header

    /// Section header styled after the handoff bundle: kind marker, uppercase
    /// sans label, monospaced count, optional hint on the right, sitting on a
    /// hairline rule.
    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            KindMarker(kind: section.kind, size: 14)
                .frame(width: 15, alignment: .leading)
                .alignmentGuide(.firstTextBaseline) { d in d[VerticalAlignment.center] + 4 }
            Text(section.title.uppercased())
                .font(DS.sans(12, weight: .medium))
                .tracking(0.05 * 12)   // letter-spacing: 0.04em → 0.48
                .foregroundStyle(DS.Ink.p2)
            if showCount {
                Text("\(section.tasks.count)")
                    .font(DS.mono(11, weight: .medium))
                    .foregroundStyle(DS.Ink.p4)
            }
            Spacer(minLength: 0)
            if let hint {
                Text(hint)
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Ink.p4)
            }
        }
        .padding(.top, 28)
        .padding(.bottom, 8)
        .overlay(alignment: .bottom) {
            EditorialRule()
        }
    }

    private var showCount: Bool {
        !section.tasks.isEmpty && section.kind != .focus && section.kind != .digest && section.kind != .meetings
    }

    private var hint: String? {
        switch section.kind {
        case .urgent:   return "act today"
        case .todo:     return "this week"
        case .watching: return "no action required"
        case .focus:    return "ordered by weight"
        case .meetings: return "today"
        case .digest:   return "end-of-day synthesis"
        default:        return nil
        }
    }

    // MARK: - Focus (numbered editorial list)

    private var focus: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(section.bullets.enumerated()), id: \.offset) { _, bullet in
                let split = ActionItemsParser.focusOrdinal(bullet)
                if let n = split.number {
                    focusItem(n: n, text: split.text)
                } else {
                    focusProse(split.text)
                }
            }
        }
        .padding(.top, 12)
    }

    /// A ranked line from the section's numbered list. The number is the
    /// source's own — the section is authored as a markdown ordered list, so
    /// counting rows here instead would both renumber it and print the ordinal
    /// twice.
    ///
    /// The top three get the priority-hue left border, mirroring the f1/f2/f3
    /// levels in the handoff bundle.
    private func focusItem(n: Int, text: String) -> some View {
        let accent: Color = {
            switch n {
            case 1:  return DS.Priority.urgent
            case 2:  return DS.Priority.todo
            case 3:  return DS.Priority.watch
            default: return DS.Rule.hard
            }
        }()
        return HStack(alignment: .top, spacing: 10) {
            Text("\(n)")
                .font(DS.mono(11, weight: .medium))
                .foregroundStyle(DS.Ink.p4)
                .frame(width: 22, alignment: .trailing)
                .padding(.top, 3)
            InlineMarkdownText(text)
                .font(DS.serif(14))
                .foregroundStyle(DS.Ink.p1)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 14)
        .background {
            Rectangle()
                .fill(DS.Paper.raised.opacity(0.6))
                .clipShape(RoundedCorners(radius: 6, corners: [.topRight, .bottomRight]))
        }
        .overlay(alignment: .leading) {
            Rectangle().fill(accent).frame(width: 2)
        }
    }

    /// The narrative around the list — the run's lede above it, the
    /// `⏸️ Verified negatives` note below. Prose, not a ranked item, so it gets
    /// no number and no card.
    private func focusProse(_ text: String) -> some View {
        InlineMarkdownText(text)
            .font(DS.serif(13))
            .foregroundStyle(DS.Ink.p2)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
    }

    // MARK: - Recently completed

    private var completedList: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(section.tasks) { t in
                    HStack(alignment: .top, spacing: 8) {
                        if selection != nil {
                            completedSelectionButton(t)
                        }
                        Image(systemName: "checkmark")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(DS.Priority.done)
                        InlineMarkdownText(t.subject)
                            .font(DS.serif(13))
                            .strikethrough(color: DS.Ink.p4)
                            .foregroundStyle(DS.Ink.p3)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.vertical, 4)
                }
            }
            .padding(.top, 6)
        } label: {
            HStack(spacing: 8) {
                Text("RECENTLY COMPLETED")
                    .font(DS.sans(12, weight: .medium))
                    .tracking(0.05 * 12)
                    .foregroundStyle(DS.Ink.p3)
                Text("\(section.tasks.count)")
                    .font(DS.mono(11, weight: .medium))
                    .foregroundStyle(DS.Ink.p4)
                Spacer()
            }
        }
    }

    private func completedSelectionButton(_ task: ActionTask) -> some View {
        let selected = selection?.wrappedValue.contains(task.id) == true
        return Button {
            guard let selection else { return }
            if selected {
                selection.wrappedValue.remove(task.id)
            } else {
                selection.wrappedValue.insert(task.id)
            }
        } label: {
            Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(selected ? DS.Accent.ink : DS.Ink.p4)
                .frame(width: 26, height: 24)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plainHit)
        .help(selected ? "Remove from copy selection" : "Add to copy selection")
        .accessibilityLabel(selected ? "Selected for copying" : "Select for copying")
    }
}

// MARK: - Geometry helper

/// Rounds the right side of the focus-item background so the accent stripe on
/// the left bleeds flush against the rule.
private struct RoundedCorners: Shape {
    var radius: CGFloat
    var corners: RectCorner

    struct RectCorner: OptionSet {
        let rawValue: Int
        static let topLeft     = RectCorner(rawValue: 1 << 0)
        static let topRight    = RectCorner(rawValue: 1 << 1)
        static let bottomLeft  = RectCorner(rawValue: 1 << 2)
        static let bottomRight = RectCorner(rawValue: 1 << 3)
    }

    func path(in rect: CGRect) -> Path {
        let tl = corners.contains(.topLeft)     ? radius : 0
        let tr = corners.contains(.topRight)    ? radius : 0
        let bl = corners.contains(.bottomLeft)  ? radius : 0
        let br = corners.contains(.bottomRight) ? radius : 0

        var path = Path()
        path.move(to: CGPoint(x: rect.minX + tl, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX - tr, y: rect.minY))
        if tr > 0 {
            path.addArc(center: CGPoint(x: rect.maxX - tr, y: rect.minY + tr),
                        radius: tr, startAngle: .degrees(-90), endAngle: .degrees(0), clockwise: false)
        }
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - br))
        if br > 0 {
            path.addArc(center: CGPoint(x: rect.maxX - br, y: rect.maxY - br),
                        radius: br, startAngle: .degrees(0), endAngle: .degrees(90), clockwise: false)
        }
        path.addLine(to: CGPoint(x: rect.minX + bl, y: rect.maxY))
        if bl > 0 {
            path.addArc(center: CGPoint(x: rect.minX + bl, y: rect.maxY - bl),
                        radius: bl, startAngle: .degrees(90), endAngle: .degrees(180), clockwise: false)
        }
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + tl))
        if tl > 0 {
            path.addArc(center: CGPoint(x: rect.minX + tl, y: rect.minY + tl),
                        radius: tl, startAngle: .degrees(180), endAngle: .degrees(270), clockwise: false)
        }
        path.closeSubpath()
        return path
    }
}
