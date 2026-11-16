import SwiftUI

/// Renders a per-file item body as a vertical stack of headings, task lists,
/// prose paragraphs (inline markdown) and verbatim code panels. Mirrors
/// `ProposalBodyView` from the Proposals module.
struct MarkdownBodyView: View {
    let blocks: [MarkdownBodyBlock]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // By position: two blocks can have the same text (two "## Notes").
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                switch block {
                case .prose(let text):
                    InlineMarkdownText(text)
                        .font(DS.serif(13.5))
                        .foregroundStyle(DS.Ink.p2)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                case .code(_, let code):
                    codePanel(code)
                case .heading(let level, let text):
                    heading(level: level, text: text)
                case .tasks(let tasks):
                    taskList(tasks)
                }
            }
        }
    }

    private func heading(level: Int, text: String) -> some View {
        InlineMarkdownText(text)
            .font(DS.serif(level <= 2 ? 15 : 13.5, weight: .semibold))
            .foregroundStyle(DS.Ink.p1)
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 4)
            .accessibilityAddTraits(.isHeader)
    }

    /// Read-only checkboxes: the file is the source of truth, and the engine
    /// or the owner ticks a sub-task by editing it.
    private func taskList(_ tasks: [MarkdownTask]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(tasks.enumerated()), id: \.offset) { _, task in
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    Image(systemName: task.checked ? "checkmark.square.fill" : "square")
                        .font(.system(size: 11))
                        .foregroundStyle(task.checked ? DS.Status.ok : DS.Ink.p3)
                        .accessibilityHidden(true)
                    InlineMarkdownText(task.text)
                        .font(DS.serif(13.5))
                        .foregroundStyle(task.checked ? DS.Ink.p3 : DS.Ink.p2)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.leading, CGFloat(task.depth) * 16)
                .accessibilityElement(children: .combine)
                .accessibilityValue(task.checked ? "Done" : "Not done")
            }
        }
    }

    private func codePanel(_ code: String) -> some View {
        Text(code)
            .font(DS.mono(12))
            .foregroundStyle(DS.Ink.p1)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .neumorphicPressed(cornerRadius: 6)
            .fixedSize(horizontal: false, vertical: true)
    }
}
