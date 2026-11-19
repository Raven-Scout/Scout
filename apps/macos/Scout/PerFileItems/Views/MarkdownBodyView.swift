import SwiftUI

/// Renders a per-file item body as a vertical stack of headings, task lists,
/// quotes and callouts, prose paragraphs (inline markdown) and verbatim code
/// panels. Mirrors
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
                        .dsFont(.body, .serif)
                        .foregroundStyle(DS.Ink.p2)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                case .code(_, let code):
                    codePanel(code)
                case .heading(let level, let text):
                    heading(level: level, text: text)
                case .tasks(let tasks):
                    taskList(tasks)
                case .quote(let kind, let title, let text):
                    quote(kind: kind, title: title, text: text)
                }
            }
        }
    }

    private func heading(level: Int, text: String) -> some View {
        InlineMarkdownText(text)
            .dsFont(level <= 2 ? .headline : .body, .serif, weight: .semibold)
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
                        .font(DS.font(.caption))
                        .foregroundStyle(task.checked ? DS.Status.ok : DS.Ink.p3)
                        .accessibilityHidden(true)
                    InlineMarkdownText(task.text)
                        .dsFont(.body, .serif)
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

    /// A quote, or a callout with its title: a rule down the leading edge,
    /// tinted by the callout's kind, beside the text.
    private func quote(kind: String?, title: String?, text: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 1)
                .fill(Self.tint(forCallout: kind))
                .frame(width: 3)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                if let title {
                    InlineMarkdownText(title)
                        .font(DS.font(.label, weight: .semibold))
                        .foregroundStyle(DS.Ink.p1)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !text.isEmpty {
                    InlineMarkdownText(text)
                        .dsFont(.body, .serif)
                        .foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .textSelection(.enabled)
        }
        .fixedSize(horizontal: false, vertical: true)
        // Combine title and body, then say what the block is in front of them:
        // the plain `accessibilityLabel(_:)` would replace the body.
        .accessibilityElement(children: .combine)
        .accessibilityLabel(content: { label in
            Text(Self.accessibilityPrefix(kind: kind))
            label
        })
    }

    /// What VoiceOver says before a quote's text: "Warning: " for a callout
    /// (its title follows), "Quote: " for a plain quote.
    nonisolated static func accessibilityPrefix(kind: String?) -> String {
        (kind == nil ? "Quote" : calloutLabel(kind)) + ": "
    }

    /// Warnings read amber, dangers red, tips green; anything else, and a
    /// plain quote, takes the rule colour.
    static func tint(forCallout kind: String?) -> Color {
        switch kind {
        case "warning", "caution", "attention": return DS.Status.warn
        case "danger", "error", "bug", "failure", "fail": return DS.Status.err
        case "tip", "hint", "success", "check", "done": return DS.Status.ok
        case nil: return DS.Rule.hard
        default: return DS.Accent.fill
        }
    }

    /// What VoiceOver calls the block: "Warning", "Note", or "Callout".
    nonisolated static func calloutLabel(_ kind: String?) -> String {
        guard let kind, !kind.isEmpty else { return "Callout" }
        return kind.prefix(1).uppercased() + kind.dropFirst()
    }

    private func codePanel(_ code: String) -> some View {
        Text(code)
            .font(DS.font(.label, .mono))
            .foregroundStyle(DS.Ink.p1)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .neumorphicPressed(cornerRadius: 6)
            .fixedSize(horizontal: false, vertical: true)
    }
}
