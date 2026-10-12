import SwiftUI

/// One step on the topic board: a fixed-height two-line row that expands to
/// the step's detail, its sub-steps and the steps it blocks.
struct StepRowView: View {
    static let rowHeight: CGFloat = 50

    let step: BoardStep
    let topic: BoardTopic?
    let scoutDirectory: URL

    @AppStorage("topicBoard.open") private var openRaw: String = ""
    @State private var asking = false

    private var task: ActionTask { step.item.task }
    private var rootSession: String? { topic?.meta?.session }

    /// A Mine step without sub-steps names what to do next.
    static func actionLabel(for step: BoardStep) -> String? {
        guard step.item.state == .mine, step.children.isEmpty else { return nil }
        return step.item.task.fields.action ?? ActionChip.fallback
    }

    private func prompt() -> String {
        BoardPrompt.step(
            step.item,
            topic: topic?.title,
            contextNote: TaskContextNote.load(tag: task.shortPrefix, scoutDirectory: scoutDirectory)
        )
    }

    static func expansionKey(_ key: String) -> String { "step:\(key)" }

    private var expanded: Binding<Bool> {
        let key = Self.expansionKey(step.item.key)
        return Binding(
            get: { BoardExpansion.contains(key, in: openRaw) },
            set: { openRaw = BoardExpansion.setting(key, open: $0, in: openRaw) }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            DisclosureRow(
                height: Self.rowHeight,
                label: "\(task.plainSubject), \(step.item.state.label)",
                expanded: expanded,
                onAsk: { asking = true }
            ) { _ in
                summary
            }
            if asking {
                PromptBox(
                    context: prompt,
                    session: task.fields.session,
                    scoutDirectory: scoutDirectory,
                    isOpen: $asking
                )
            }
            if expanded.wrappedValue {
                StepDetailView(
                    task: task,
                    rootSession: rootSession,
                    prompt: prompt,
                    scoutDirectory: scoutDirectory
                )
                if !step.nested.isEmpty {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(step.nested) { kid in
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
        VStack(alignment: .leading, spacing: 1) {
            HStack(alignment: .top, spacing: 10) {
                ScrollView(.horizontal, showsIndicators: false) {
                    Text(task.plainSubject)
                        .font(DS.sans(13.5, weight: .medium))
                        .foregroundStyle(DS.Ink.p1)
                        .lineLimit(1)
                        .fixedSize()
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                StepTagsView(step: step)
            }
            HStack(spacing: 6) {
                StateDot(state: step.item.state, size: 7)
                if let actionLabel = Self.actionLabel(for: step) {
                    ActionChip(label: actionLabel) { asking = true }
                }
                Text(task.summary.isEmpty ? step.item.state.label : task.summary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .help(task.summary)
            }
            .font(DS.sans(11.5))
            .foregroundStyle(step.item.state == .blocked ? DS.Ink.p3 : step.item.state.color)
        }
    }
}
