import SwiftUI

// MARK: - Design-system v2 banner
//
// One banner for every message that sits above or inside a page: on battery,
// a connector is failing, the schedule file changed on disk, a run went
// wrong. Pick the tone; the component draws the rest:
//
//   DSBanner(tone: .warning, title: "Edits weren't saved",
//            message: "…", actions: [.init("Reload") { … }], onDismiss: { … })
//
//   DSBanner(tone: .error, accessibilityTitle: problem.title) { …custom lines… }
//
// A rounded wash of the tone's colour with a hairline of it, the tone's
// symbol, text in the paper inks (never the tone colour, so it stays
// readable), the first action as a small primary button and the rest as
// small secondary ones, and a quiet × when it can be dismissed.
// `ProblemView` draws its banner style through this. `BannerStyleGuardTests`
// keeps hand-built banners (a status-coloured wash behind a row) out.

extension DS {
    /// What a banner is telling you.
    nonisolated enum BannerTone: CaseIterable, Sendable {
        case info, warning, error

        /// The colour the wash, hairline and symbol are drawn in.
        var tint: Swatch {
            switch self {
            case .info:    return Palette.statusInfo
            case .warning: return Palette.statusWarn
            case .error:   return Palette.statusErr
            }
        }

        var symbol: String {
            switch self {
            case .info:    return "info.circle.fill"
            case .warning: return "exclamationmark.triangle.fill"
            case .error:   return "xmark.octagon.fill"
            }
        }

        /// The word VoiceOver reads before the title.
        var word: String {
            switch self {
            case .info:    return "Note"
            case .warning: return "Warning"
            case .error:   return "Problem"
            }
        }
    }

    /// The banner wash's opacity over the paper, and its hairline's.
    nonisolated static let bannerWashOpacity = 0.08
    nonisolated static let bannerStrokeOpacity = 0.35
    static let bannerCornerRadius: CGFloat = 8

    /// The text colours a banner draws on its wash: the title and "what to
    /// do" in the primary ink, the explanation in the secondary.
    nonisolated static let bannerText: [Swatch] = [Palette.inkP1, Palette.inkP2]
}

/// The design system's banner. See the overview above.
struct DSBanner<Content: View>: View {
    let tone: DS.BannerTone
    var symbol: String? = nil
    /// What VoiceOver reads after the tone's word ("Warning: …").
    let accessibilityTitle: String
    var onDismiss: (() -> Void)? = nil
    @ViewBuilder var content: Content

    init(tone: DS.BannerTone, symbol: String? = nil, accessibilityTitle: String,
         onDismiss: (() -> Void)? = nil, @ViewBuilder content: () -> Content) {
        self.tone = tone
        self.symbol = symbol
        self.accessibilityTitle = accessibilityTitle
        self.onDismiss = onDismiss
        self.content = content()
    }

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: DS.bannerCornerRadius)
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol ?? tone.symbol)
                .foregroundStyle(tone.tint.color)
                .font(DS.font(.body, weight: .semibold))
                .accessibilityHidden(true)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 4) {
                content
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if let onDismiss {
                Button(action: onDismiss) {
                    Image(systemName: "xmark").font(DS.font(.caption2, weight: .semibold))
                }
                .buttonStyle(.ds(.quiet, size: .small))
                .help("Dismiss")
                .accessibilityLabel("Dismiss")
            }
        }
        .padding(12)
        .background(
            shape.fill(tone.tint.color.opacity(DS.bannerWashOpacity))
                .overlay(shape.strokeBorder(tone.tint.color.opacity(DS.bannerStrokeOpacity), lineWidth: 1))
        )
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(tone.word): \(accessibilityTitle)")
    }
}

/// A banner's standard lines: a title, an optional message, and buttons.
/// Backtick code spans (engine text names commands that way) show in
/// monospace without their backticks (UI audit T3).
struct DSBannerText: View {
    let title: String
    var message: String? = nil
    var actions: [ProblemAction] = []

    var body: some View {
        Text(PlainText.codeSpans(title))
            .font(DS.font(.label, weight: .semibold))
            .foregroundStyle(DS.Ink.p1)
            .fixedSize(horizontal: false, vertical: true)
        if let message {
            Text(PlainText.codeSpans(message))
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
        }
        if !actions.isEmpty {
            DSBannerActions(actions: actions)
        }
    }
}

/// A banner's buttons: the first is the primary action.
struct DSBannerActions<Trailing: View>: View {
    let actions: [ProblemAction]
    @ViewBuilder var trailing: Trailing

    init(actions: [ProblemAction], @ViewBuilder trailing: () -> Trailing) {
        self.actions = actions
        self.trailing = trailing()
    }

    var body: some View {
        HStack(spacing: 8) {
            ForEach(Array(actions.enumerated()), id: \.element.id) { i, action in
                Button(action.title) { action.perform() }
                    .buttonStyle(.ds(i == 0 ? .primary : .secondary, size: .small))
            }
            trailing
        }
        .padding(.top, 2)
    }
}

extension DSBannerActions where Trailing == EmptyView {
    init(actions: [ProblemAction]) {
        self.init(actions: actions) { EmptyView() }
    }
}

extension DSBanner where Content == DSBannerText {
    /// A banner with a title, an optional message and buttons.
    init(tone: DS.BannerTone, title: String, message: String? = nil, symbol: String? = nil,
         actions: [ProblemAction] = [], onDismiss: (() -> Void)? = nil) {
        self.init(tone: tone, symbol: symbol, accessibilityTitle: title, onDismiss: onDismiss) {
            DSBannerText(title: title, message: message, actions: actions)
        }
    }
}

#if DEBUG
#Preview("Banners") {
    VStack(spacing: 12) {
        DSBanner(tone: .info, title: "Scout is on battery", message: "Scheduled runs wait for power.")
        DSBanner(tone: .warning, title: "The schedule changed on disk",
                 actions: [ProblemAction("Reload now") {}], onDismiss: {})
        DSBanner(tone: .error, title: "Gmail stopped working", message: "Sign in again in Settings.",
                 actions: [ProblemAction("Open Settings") {}, ProblemAction("Details") {}])
    }
    .padding()
    .frame(width: 520)
}
#endif
