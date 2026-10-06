import SwiftUI

/// A task's indented sub-bullets, in source order, as a muted bullet list —
/// the context an item written as a title plus sub-bullets carries.
struct TaskDetailsView: View {
    let details: [TaskDetail]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(details.enumerated()), id: \.offset) { _, detail in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text("•")
                        .font(DS.serif(13))
                        .foregroundStyle(DS.Ink.p4)
                    InlineMarkdownText(detail.text)
                        .font(DS.serif(13))
                        .foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.leading, CGFloat(detail.depth) * 14)
            }
        }
    }
}
