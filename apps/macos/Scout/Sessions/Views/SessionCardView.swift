import SwiftUI

/// One session on the board (spec §6.2), styled after `BoardCardView`: a 3 pt
/// state edge and the state in words with its symbol and age, a two-line
/// serif title, the deciding reason, then PR, branch and model.
struct SessionCardView: View {
    let session: AgentSession
    let now: Date
    /// Set on a Now-strip card, which sits outside its project's lane.
    var projectName: String? = nil
    /// Set for a sub-agent whose parent is in the index.
    var parentTitle: String? = nil
    var isSelected = false
    /// This card is the parent of the card under the pointer.
    var isHighlighted = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let projectName {
                Text(projectName)
                    .font(DS.sans(10.5, weight: .medium))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .accessibilityLabel("Project: \(projectName)")
            }
            if let parentTitle {
                Text("↳ \(parentTitle)")
                    .font(DS.sans(10.5))
                    .foregroundStyle(DS.Ink.p4)
                    .lineLimit(1)
            }
            HStack(spacing: 8) {
                SessionStateLabel(state: session.state, isOpen: session.isOpen)
                Spacer(minLength: 0)
                Text(SessionsFormat.ago(session.lastActivityAt, now: now))
                    .font(DS.sans(10.5))
                    .foregroundStyle(DS.Ink.p4)
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
            if let pr = session.pr {
                Text(SessionsFormat.prChip(pr))
                    .font(DS.mono(10.5))
                    .foregroundStyle(DS.Ink.p2)
                    .lineLimit(1)
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
        // The state is read from its label above, not repeated as a value.
        .accessibilityElement(children: .combine)
    }

    private var borderColor: Color {
        if isSelected { return DS.Ink.p2 }
        if isHighlighted { return DS.Accent.fill }
        return DS.Rule.soft
    }
}
