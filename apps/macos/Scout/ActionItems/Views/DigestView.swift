import SwiftUI

/// End-of-day synthesis. Editorial voice: serif body, left-rule indent,
/// small-caps subheads via `**bold**` lines.
struct DigestView: View {
    let section: ActionSection

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(section.bullets.enumerated()), id: \.offset) { _, b in
                if isBoldOnly(b) {
                    InlineMarkdownText(b)
                        .font(DS.sans(12, weight: .medium))
                        .tracking(0.04 * 12)
                        .foregroundStyle(DS.Ink.p3)
                        .padding(.top, 10)
                } else {
                    InlineMarkdownText(b)
                        .font(DS.serif(14.5))
                        .foregroundStyle(DS.Ink.p2)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(.leading, 18)
        .overlay(alignment: .leading) {
            Rectangle().fill(DS.Rule.hard).frame(width: 2)
        }
        .padding(.top, 8)
    }

    /// Lines matching ``^\*\*[^*]+\*\*:?\s*$`` are subheads in the Python renderer.
    private func isBoldOnly(_ s: String) -> Bool {
        guard let re = try? NSRegularExpression(pattern: #"^\*\*[^*]+\*\*:?\s*$"#) else { return false }
        return re.firstMatch(in: s, range: NSRange(location: 0, length: (s as NSString).length)) != nil
    }
}
