import SwiftUI

/// A single collapsible briefing-preamble card. Shows the headline always;
/// hides the body behind a chevron so the top of the page stays scannable
/// even when Scout writes 3 dense paragraphs back-to-back.
///
/// Behavior:
///   - Click anywhere on the card to toggle.
///   - Collapsed: show the headline + a 2-line preview of the body, faded.
///   - Expanded: render the full body via `InlineMarkdownText` so wikilinks,
///     `code`, **bold**, and italics still work.
struct PreambleCard: View {
    let headline: String
    /// Body text of the paragraph (renamed from `body` to avoid colliding
    /// with SwiftUI's `var body: some View`).
    let text: String
    let defaultExpanded: Bool

    @State private var isExpanded: Bool
    @State private var hovering: Bool = false

    init(headline: String, body: String, defaultExpanded: Bool) {
        self.headline = headline
        self.text = body
        self.defaultExpanded = defaultExpanded
        self._isExpanded = State(initialValue: defaultExpanded)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Headline row — chevron · bold serif headline.
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { isExpanded.toggle() }
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 9) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(DS.Ink.p3)
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                        .frame(width: 12)
                    InlineMarkdownText(headline)
                        .font(DS.serif(14.5, weight: .medium))
                        .foregroundStyle(DS.Ink.p1)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.vertical, 10)
                .padding(.horizontal, 12)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plainHit)
            .onHover { hovering = $0 }

            // Body, conditional. Two-line preview when collapsed; full body
            // when expanded.
            if isExpanded {
                InlineMarkdownText(text)
                    .font(DS.serif(13.5))
                    .foregroundStyle(DS.Ink.p2)
                    .lineSpacing(2.5)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 12)
                    .padding(.leading, 12)   // align with headline text past the chevron
            } else if !text.isEmpty {
                InlineMarkdownText(bodyTeaser(text))
                    .font(DS.serif(13))
                    .foregroundStyle(DS.Ink.p3)
                    .lineSpacing(2)
                    .lineLimit(2)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 10)
                    .padding(.leading, 12)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(hovering ? DS.Paper.raised : DS.Paper.raised.opacity(0.5))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
    }

    /// Strip markdown ornament from the body and clamp to a single-pass
    /// preview suitable for the 2-line truncated display. Keeps the visual
    /// hierarchy quiet — readers see narrative, not formatting bullets.
    private func bodyTeaser(_ raw: String) -> String {
        raw
            .replacingOccurrences(of: "**", with: "")
            .replacingOccurrences(of: "__", with: "")
            // Collapse any whitespace runs (briefings sometimes have hard
            // breaks inside one logical paragraph).
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }
}
