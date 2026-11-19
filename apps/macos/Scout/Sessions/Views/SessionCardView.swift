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
    /// Hover from the `.dsCard` button around this card.
    @Environment(\.dsCardHovered) private var hovered

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let projectName {
                Text(projectName)
                    .font(DS.font(.caption2, weight: .medium))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .accessibilityLabel("Project: \(projectName)")
            }
            if let parentTitle {
                Text("↳ \(parentTitle)")
                    .font(DS.font(.caption2))
                    .foregroundStyle(DS.Ink.p4)
                    .lineLimit(1)
            }
            HStack(spacing: 8) {
                SessionStateLabel(state: session.state, isOpen: session.isOpen)
                Spacer(minLength: 0)
                Text(SessionsFormat.ago(session.lastActivityAt, now: now))
                    .font(DS.font(.caption2))
                    .foregroundStyle(DS.Ink.p4)
            }
            Text(session.displayTitle)
                .dsFont(.body, .serif, weight: .medium)
                .foregroundStyle(DS.Ink.p1)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let reason = session.primaryReason {
                Text(reason)
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(1)
            }
            if let pr = session.pr {
                Text(SessionsFormat.prChip(pr))
                    .font(DS.font(.caption2, .mono))
                    .foregroundStyle(DS.Ink.p2)
                    .lineLimit(1)
            }
            HStack(spacing: 6) {
                if let branch = session.worktree?.branch {
                    Text(branch)
                        .font(DS.font(.caption2, .mono))
                        .foregroundStyle(DS.Ink.p3)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer(minLength: 0)
                if let model = session.model {
                    Text(SessionsFormat.shortModel(model))
                        .font(DS.font(.caption2, .mono))
                        .foregroundStyle(DS.Ink.p4)
                }
            }
        }
        // The parent of the hovered card is highlighted the way a hovered
        // card is; the selected one is outlined.
        .editorialCard(padding: 12, selected: isSelected,
                       accentBar: SessionStateStyle.color(session.state, isOpen: session.isOpen))
        .dsCardHovered(hovered || isHighlighted)
        .frame(width: 240, alignment: .leading)
        // The state is read from its label above, not repeated as a value.
        .accessibilityElement(children: .combine)
    }

}
