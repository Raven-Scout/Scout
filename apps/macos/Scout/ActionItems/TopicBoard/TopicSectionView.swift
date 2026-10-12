import SwiftUI

/// One workstream: a fixed-height header row, collapsed by default, over its steps.
struct TopicSectionView: View {
    static let headerHeight: CGFloat = 54

    let topic: BoardTopic
    let scoutDirectory: URL
    @Binding var expanded: Bool
    /// `nil` hides the rename pencil (the engine can't save a rename).
    var onRename: ((String) -> Void)?

    @State private var asking = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            DisclosureRow(
                height: Self.headerHeight,
                label: topic.title,
                expanded: $expanded,
                onAsk: { asking = true }
            ) { hovered in
                header(hovered: hovered)
            }
            if asking {
                PromptBox(
                    context: { BoardPrompt.topic(topic) },
                    session: topic.meta?.session,
                    scoutDirectory: scoutDirectory,
                    isOpen: $asking
                )
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
        .boardCard()
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
        HStack(spacing: 6) {
            if let session = topic.meta?.session {
                SessionLink(session: session)
            }
            if let summary = topic.meta?.summary {
                Text(summary)
            }
        }
    }
}

extension View {
    /// The raised, outlined card a topic or a standalone step sits on.
    func boardCard() -> some View {
        background(RoundedRectangle(cornerRadius: 10).fill(DS.Paper.raised))
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(DS.Rule.soft, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}
