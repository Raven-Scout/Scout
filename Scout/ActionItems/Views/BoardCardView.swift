import SwiftUI

/// A compact, read-only card on the Action Items board (issue #15). Shows the
/// task's short prefix, subject, and a footer of status/source affordances.
/// No mutation surfaces — the List view remains the place to act on a task.
struct BoardCardView: View {
    let task: ActionTask
    let kind: ActionSection.Kind

    private var effectiveKind: ActionSection.Kind { task.snoozedFromKind ?? kind }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            InlineMarkdownText(task.subject)
                .font(DS.serif(13.5, weight: .medium))
                .foregroundStyle(task.done ? DS.Ink.p3 : DS.Ink.p1)
                .strikethrough(task.done, color: DS.Ink.p4)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            footer
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2)
                .fill(DS.priorityColor(effectiveKind))
                .frame(width: 3)
                .padding(.vertical, 6)
                .opacity(task.done ? 0.5 : 1)
        }
    }

    private var header: some View {
        HStack(spacing: 6) {
            if let prefix = task.shortPrefix {
                Text("#\(prefix)")
                    .font(DS.mono(10.5, weight: .medium))
                    .foregroundStyle(DS.Ink.p4)
            }
            Spacer(minLength: 0)
            if task.done {
                Image(systemName: "checkmark.circle.fill")
                    .imageScale(.small)
                    .foregroundStyle(DS.Status.ok)
            } else if task.snoozedUntil != nil {
                Image(systemName: "moon.zzz.fill")
                    .imageScale(.small)
                    .foregroundStyle(DS.Ink.p4)
            }
        }
    }

    @ViewBuilder
    private var footer: some View {
        if !task.deepLinks.isEmpty {
            HStack(spacing: 8) {
                ForEach(task.deepLinks) { link in
                    HStack(spacing: 3) {
                        Image(systemName: linkGlyph(link))
                            .imageScale(.small)
                        Text(link.displayLabel)
                            .lineLimit(1)
                    }
                    .font(DS.mono(10))
                    .foregroundStyle(DS.Ink.p3)
                }
            }
        }
    }

    private func linkGlyph(_ link: TaskDeepLink) -> String {
        switch link {
        case .linear:      return "circle.grid.2x2"
        case .githubPR:    return "arrow.triangle.pull"
        case .slackThread: return "bubble.left.and.bubble.right"
        case .entity:      return "doc.text"
        case .crossRef:    return "number.square"
        case .plainRef:    return "tag"
        }
    }
}
