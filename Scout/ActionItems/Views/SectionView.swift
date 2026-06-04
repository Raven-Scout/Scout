import SwiftUI

struct SectionView: View {
    let section: ActionSection
    let displayedDate: Date
    let scoutDirectory: URL
    let onOp: (WriteOp) async throws -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            switch section.kind {
            case .focus:
                focus
            case .meetings:
                MeetingsTableView(section: section)
                    .padding(.top, 4)
            case .done:
                completedList
            case .digest:
                DigestView(section: section)
            default:
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(section.tasks) { task in
                        TaskCardView(
                            task: task,
                            kind: section.kind,
                            displayedDate: displayedDate,
                            scoutDirectory: scoutDirectory,
                            onOp: onOp
                        )
                    }
                }
                .frame(maxWidth: .infinity, alignment: .top)
                .padding(.top, 12)
            }
        }
        .padding(.bottom, 12)
    }

    // MARK: - Header

    /// Section header styled after the handoff bundle: small glyph, uppercase
    /// sans label, monospaced count, optional hint on the right, sitting on a
    /// hairline rule.
    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(glyph)
                .font(DS.sans(13))
                .frame(width: 18, alignment: .leading)
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

    private var glyph: String {
        section.emoji.isEmpty ? DS.kindGlyph(section.kind) : section.emoji
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
            ForEach(Array(section.bullets.enumerated()), id: \.offset) { index, bullet in
                focusItem(n: index + 1, bullet: bullet, index: index)
            }
        }
        .padding(.top, 12)
    }

    /// First three focus items get the priority-hue left border — mirrors the
    /// f1/f2/f3 levels in the handoff bundle.
    @ViewBuilder
    private func focusItem(n: Int, bullet: String, index: Int) -> some View {
        let accent: Color = {
            switch index {
            case 0:  return DS.Priority.urgent
            case 1:  return DS.Priority.todo
            case 2:  return DS.Priority.watch
            default: return DS.Rule.hard
            }
        }()
        HStack(alignment: .top, spacing: 10) {
            Text("\(n)")
                .font(DS.mono(11, weight: .medium))
                .foregroundStyle(DS.Ink.p4)
                .frame(width: 22, alignment: .trailing)
                .padding(.top, 3)
            InlineMarkdownText(bullet)
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

    // MARK: - Recently completed

    private var completedList: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(section.tasks) { t in
                    HStack(alignment: .top, spacing: 8) {
                        Text("✓")
                            .font(DS.mono(11))
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
