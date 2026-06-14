import SwiftUI

/// Renders a proposal body as a vertical stack of prose paragraphs (inline
/// markdown) and verbatim code panels. Keeps the dense bold-label-and-code
/// proposal text readable instead of collapsing it into one wall.
struct ProposalBodyView: View {
    let blocks: [ProposalBodyBlock]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(blocks) { block in
                switch block {
                case .prose(let text):
                    InlineMarkdownText(text)
                        .font(DS.serif(13.5))
                        .foregroundStyle(DS.Ink.p2)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                case .code(_, let code):
                    codePanel(code)
                }
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
