import SwiftUI

/// One workstream: a fixed-height header row, collapsed by default, over its steps.
struct TopicSectionView: View {
    static let headerHeight: CGFloat = 54

    let topic: BoardTopic
    let scoutDirectory: URL
    @Binding var expanded: Bool
    /// `nil` hides the rename pencil (the engine can't save a rename).
    var onRename: ((String) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            DisclosureRow(
                height: Self.headerHeight,
                label: topic.title,
                expanded: $expanded,
                prompt: { BoardPrompt.topic(topic) },
                session: topic.meta?.session,
                scoutDirectory: scoutDirectory
            ) { hovered in
                header(hovered: hovered)
            }
            if expanded {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(topic.steps) { step in
                        Divider().overlay(DS.Rule.soft)
                        StepRowView(step: step, topic: topic, scoutDirectory: scoutDirectory)
                    }
                }
            }
        }
        .background(RoundedRectangle(cornerRadius: 10).fill(DS.Paper.raised))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(DS.Rule.soft, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func header(hovered: Bool) -> some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                TopicNameField(
                    title: topic.title,
                    scoutTitle: topic.meta?.title ?? topic.title,
                    canRename: topic.meta != nil && onRename != nil,
                    rowHovered: hovered,
                    onRename: { onRename?($0) }
                )
                .font(DS.sans(15, weight: .semibold))
                .foregroundStyle(DS.Ink.p1)
                subtitle
                    .font(DS.sans(11.5))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            TopicCountsView(counts: topic.counts)
        }
    }

    @ViewBuilder
    private var subtitle: some View {
        if let session = topic.meta?.session {
            SessionLink(session: session)
        } else if let summary = topic.meta?.summary {
            Text(summary)
        }
    }
}
