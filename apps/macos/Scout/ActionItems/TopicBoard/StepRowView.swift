import SwiftUI

/// One step on the topic board: a fixed-height summary row that expands to
/// the step's detail and the steps it blocks.
struct StepRowView: View {
    static let rowHeight: CGFloat = 50

    let step: BoardStep
    let topic: BoardTopic?
    let scoutDirectory: URL

    @State private var expanded = false

    private var task: ActionTask { step.item.task }
    private var rootSession: String? { topic?.meta?.session }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            DisclosureRow(
                height: Self.rowHeight,
                label: "\(task.plainSubject), \(step.item.state.label)",
                expanded: $expanded,
                prompt: { BoardPrompt.step(step.item, topic: topic?.title) },
                session: task.fields.session,
                scoutDirectory: scoutDirectory
            ) { _ in
                summary
            }
            if expanded {
                StepDetailView(task: task, rootSession: rootSession, scoutDirectory: scoutDirectory)
                if !step.blocked.isEmpty {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(step.blocked) { kid in
                            StepRowView(step: kid, topic: topic, scoutDirectory: scoutDirectory)
                        }
                    }
                    .padding(.leading, 22)
                    .overlay(alignment: .leading) {
                        Rectangle().fill(DS.Rule.soft).frame(width: 2).padding(.leading, 22)
                    }
                }
            }
        }
    }

    private var summary: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(task.plainSubject)
                    .font(DS.sans(13.5, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                    .lineLimit(1)
                    .truncationMode(.tail)
                HStack(spacing: 6) {
                    StateDot(state: step.item.state, size: 7)
                    Text(task.summary.isEmpty ? step.item.state.label : task.summary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .help(task.summary)
                }
                .font(DS.sans(11.5))
                .foregroundStyle(step.item.state == .blocked ? DS.Ink.p3 : step.item.state.color)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            StepTagsView(step: step)
        }
    }
}
