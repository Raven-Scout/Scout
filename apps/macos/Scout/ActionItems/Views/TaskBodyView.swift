import SwiftUI

/// Renders a task body as structured blocks instead of one flat run. Bold-label
/// clauses become labeled paragraphs, inline `(1)…(2)…` enumerations become a
/// real numbered list, and the trailing `[[wikilink]]` cluster becomes a row of
/// context pills. See `TaskBodyParser`.
struct TaskBodyView: View {
    let rawBody: String

    private var blocks: [TaskBodyBlock] { TaskBodyParser.blocks(from: rawBody) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func blockView(_ block: TaskBodyBlock) -> some View {
        switch block {
        case .paragraph(let label, let text):
            VStack(alignment: .leading, spacing: 4) {
                if let label { eyebrow(label) }
                if !text.isEmpty {
                    InlineMarkdownText(text)
                        .font(DS.serif(13.5))
                        .foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }

        case .steps(let label, let items):
            VStack(alignment: .leading, spacing: 6) {
                if let label { eyebrow(label) }
                ForEach(Array(items.enumerated()), id: \.offset) { i, item in
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text("\(i + 1)")
                            .font(DS.mono(11, weight: .semibold))
                            .foregroundStyle(DS.Accent.ink)
                            .frame(width: 18, alignment: .trailing)
                        InlineMarkdownText(item)
                            .font(DS.serif(13.5))
                            .foregroundStyle(DS.Ink.p2)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .padding(.leading, 2)

        case .links(let targets):
            FlowLayout(spacing: 6, lineSpacing: 6) {
                ForEach(targets, id: \.self) { target in
                    contextPill(target)
                }
            }
            .padding(.top, 2)
        }
    }

    /// Small uppercase label introducing a clause — the artifact's labeled-block
    /// affordance in Scout's palette.
    private func eyebrow(_ text: String) -> some View {
        Text(text.uppercased())
            .font(DS.sans(10, weight: .semibold))
            .tracking(0.06 * 10)
            .foregroundStyle(DS.Ink.p3)
    }

    /// A trailing-wikilink target rendered as a tappable context pill. Reuses
    /// `InlineMarkdownText`'s wikilink handling (Linear/Obsidian routing) by
    /// feeding it a `[[target|display]]` link; display is the last path segment.
    private func contextPill(_ target: String) -> some View {
        let display = target.split(separator: "/").last.map(String.init) ?? target
        return InlineMarkdownText("[[\(target)|\(display)]]")
            .font(DS.mono(10.5))
            .lineLimit(1)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(EditorialChipBackground())
    }
}
