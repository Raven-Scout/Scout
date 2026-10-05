import SwiftUI

/// One session on the board (spec §6.2), styled after `BoardCardView`: a 3 pt
/// state edge, a two-line serif title, the deciding reason, then PR, age,
/// branch and model.
struct SessionCardView: View {
    let session: AgentSession
    let now: Date
    /// Set for a sub-agent whose parent is in the index.
    var parentTitle: String? = nil
    var isSelected = false
    /// This card is the parent of the card under the pointer.
    var isHighlighted = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let parentTitle {
                Text("↳ \(parentTitle)")
                    .font(DS.sans(10.5))
                    .foregroundStyle(DS.Ink.p4)
                    .lineLimit(1)
            }
            Text(session.displayTitle)
                .font(DS.serif(13.5, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let reason = session.primaryReason {
                Text(reason)
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(1)
            }
            HStack(spacing: 8) {
                if let pr = session.pr {
                    Text(SessionsFormat.prChip(pr))
                        .font(DS.mono(10.5))
                        .foregroundStyle(DS.Ink.p2)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                Text(SessionsFormat.ago(session.lastActivityAt, now: now))
                    .font(DS.sans(10.5))
                    .foregroundStyle(DS.Ink.p4)
            }
            HStack(spacing: 6) {
                if let branch = session.worktree?.branch {
                    Text(branch)
                        .font(DS.mono(10))
                        .foregroundStyle(DS.Ink.p3)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer(minLength: 0)
                if let model = session.model {
                    Text(SessionsFormat.shortModel(model))
                        .font(DS.mono(10))
                        .foregroundStyle(DS.Ink.p4)
                }
            }
        }
        .padding(12)
        .frame(width: 240, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(borderColor, lineWidth: isSelected || isHighlighted ? 1 : 0.5))
        )
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2)
                .fill(SessionStateStyle.color(session.state, isOpen: session.isOpen))
                .frame(width: 3)
                .padding(.vertical, 6)
        }
        .accessibilityElement(children: .combine)
        .accessibilityValue(session.state.label)
    }

    private var borderColor: Color {
        if isSelected { return DS.Ink.p2 }
        if isHighlighted { return DS.Accent.fill }
        return DS.Rule.soft
    }
}
