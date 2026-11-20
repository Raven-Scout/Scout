import SwiftUI

// MARK: - Design-system v2 chips, pills and tags
//
// Three small shapes, each drawing its own states, so a view picks a role and
// never a fill, stroke, font or point size:
//
//   DSPill("Approved", tone: .ok)         a read-only status: a capsule wash in
//                                         the tone's colour with a hairline of
//                                         it, the text in the tone's ink.
//   Button { … }.buttonStyle(.dsChip(selected: on))
//                                         a filter or toggle chip. Unselected is
//                                         raised paper with a hairline, selected
//                                         is inverted ink; hover, pressed,
//                                         disabled and keyboard focus are drawn
//                                         here. `DSChipCount(n)` is its count.
//   Text("PROJ-12").dsTag(.mono)          a data tag or deep link: the small
//                                         boxed label on cards (r5, raised paper,
//                                         a hard hairline).
//
// Every colour is a `DS.Palette` swatch, and `DS.ContrastPairs` lists each
// pill's text on its wash over every paper, so `ContrastPairsTests` checks
// them in light, dark and both Increase Contrast modes. `ChipStyleGuardTests`
// keeps hand-built capsules, chip boxes and tint washes out of views.

extension DS {

    /// What a status pill says about its subject. Pick the meaning; the
    /// colours follow.
    nonisolated enum PillTone: Sendable {
        /// No judgement: a policy, a tag, an unknown status.
        case neutral
        /// Open, proposed, collapsed: the editorial amber.
        case accent
        /// Done, approved, applied, up to date.
        case ok
        /// Needs a look: an update, a risk, an unprotected run.
        case warn
        /// Failed, rejected, dropped.
        case err
        /// An item's Urgent priority: the urgent red, which means only that.
        case urgent
        /// Running, in progress or pending: a calm blue.
        case info
        /// Waiting on you (`DS.Attention.you`).
        case attention
        /// A slot type's own hue, drawn solid with dark ink (the timeline).
        case slot(Scout.SlotType)

        /// Every tone, for the contrast list and the guard.
        static var all: [PillTone] {
            [.neutral, .accent, .ok, .warn, .err, .urgent, .info, .attention,
             .slot(.briefing), .slot(.consolidation), .slot(.dreaming), .slot(.research), .slot(.manual)]
        }
    }

    /// A pill's text, its wash and how strongly the wash is drawn.
    nonisolated struct PillColors: Sendable {
        let text: Swatch
        /// The colour the wash and hairline are drawn in.
        let tint: Swatch
        /// Opacity of the wash over the paper underneath (1 = solid).
        let washOpacity: Double
        /// Whether a hairline in `tint` outlines the pill.
        let outlined: Bool
    }

    nonisolated static let pillStrokeOpacity = 0.35

    nonisolated static func pillColors(_ tone: PillTone) -> PillColors {
        let P = Palette.self
        let wash = ContrastPairs.statusWashOpacity
        switch tone {
        case .neutral:   return PillColors(text: P.inkP2, tint: P.inkP3, washOpacity: wash, outlined: true)
        case .accent:    return PillColors(text: P.accentInk, tint: P.accentFill, washOpacity: wash, outlined: true)
        case .ok:        return PillColors(text: P.statusOk, tint: P.statusOk, washOpacity: wash, outlined: true)
        case .warn:      return PillColors(text: P.statusWarn, tint: P.statusWarn, washOpacity: wash, outlined: true)
        case .err:       return PillColors(text: P.statusErr, tint: P.statusErr, washOpacity: wash, outlined: true)
        case .urgent:    return PillColors(text: P.priorityUrgentInk, tint: P.priorityUrgent, washOpacity: wash, outlined: true)
        case .info:      return PillColors(text: P.statusInfo, tint: P.statusInfo, washOpacity: wash, outlined: true)
        case .attention: return PillColors(text: P.attentionYou, tint: P.attentionYou, washOpacity: wash, outlined: true)
        case .slot(let type):
            return PillColors(text: P.inkOnTint, tint: P.slotSwatch(type), washOpacity: 1, outlined: false)
        }
    }

    /// A filter chip's label, fill and outline for one state. A nil stroke
    /// draws no hairline.
    nonisolated struct ChipColors: Sendable {
        let label: Swatch
        let fill: Swatch
        let stroke: Swatch?
    }

    /// The colours a filter chip draws. Selected is inverted ink, the way the
    /// selected segment of a segmented control looks; disabled matches a
    /// disabled button.
    nonisolated static func chipColors(selected: Bool, enabled: Bool, hovering: Bool) -> ChipColors {
        let P = Palette.self
        guard enabled else { return ChipColors(label: P.inkP4, fill: P.paperSunk, stroke: P.ruleSoft) }
        if selected { return ChipColors(label: P.paperBase, fill: P.inkP1, stroke: nil) }
        return ChipColors(label: hovering ? P.inkP1 : P.inkP2, fill: P.paperRaised,
                          stroke: hovering ? P.ruleHard : P.ruleSoft)
    }

    /// A chip count's colour on the chip's fill.
    nonisolated static func chipCountColor(selected: Bool, enabled: Bool) -> Swatch {
        guard enabled else { return Palette.inkP4 }
        return selected ? Palette.paperBase : Palette.inkP3
    }

    /// A tag's label colour: data in the secondary ink, a link in the accent,
    /// an unresolved link in the faintest ink.
    nonisolated static func tagTextColor(link: Bool, enabled: Bool) -> Swatch {
        guard enabled else { return Palette.inkP4 }
        return link ? Palette.accentInk : Palette.inkP2
    }

    static let chipHeight: CGFloat = 26
    static let tagCornerRadius: CGFloat = 5
}

// MARK: - Status pill

/// A read-only status pill: "Approved", "Running", "Update available".
struct DSPill: View {
    let text: String
    var tone: DS.PillTone = .neutral
    /// An SF Symbol before the text.
    var symbol: String? = nil
    /// A coloured dot before the text, for states that already have a dot
    /// elsewhere (session states). Decorative, so any colour.
    var dot: Color? = nil

    init(_ text: String, tone: DS.PillTone = .neutral, symbol: String? = nil, dot: Color? = nil) {
        self.text = text
        self.tone = tone
        self.symbol = symbol
        self.dot = dot
    }

    var body: some View {
        let colors = DS.pillColors(tone)
        HStack(spacing: 4) {
            if let dot {
                Circle().fill(dot).frame(width: 7, height: 7).accessibilityHidden(true)
            }
            if let symbol {
                Image(systemName: symbol).accessibilityHidden(true)
            }
            Text(text).lineLimit(1)
        }
        .font(DS.font(.caption2, weight: .semibold))
        .foregroundStyle(colors.text.color)
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(DSPillShape(colors: colors))
        .fixedSize()
    }
}

/// The capsule behind a pill. Shared so a pill-shaped view that isn't text
/// (the menu-bar urgent count) draws the same wash.
struct DSPillShape: View {
    let colors: DS.PillColors

    init(colors: DS.PillColors) { self.colors = colors }
    init(tone: DS.PillTone) { self.colors = DS.pillColors(tone) }

    var body: some View {
        Capsule()
            .fill(colors.tint.color.opacity(colors.washOpacity))
            .overlay {
                if colors.outlined {
                    Capsule().strokeBorder(colors.tint.color.opacity(DS.pillStrokeOpacity), lineWidth: 0.5)
                }
            }
    }
}

// MARK: - Filter chip

/// The design system's filter / toggle chip. Put the chip's content (a dot or
/// marker, the label, a `DSChipCount`) in the button's label; the style sets
/// the font, colours, padding and every state.
struct DSChipStyle: ButtonStyle {
    var selected: Bool

    func makeBody(configuration: Configuration) -> some View {
        DSChipBody(configuration: configuration, selected: selected)
    }
}

private struct DSChipBody: View {
    let configuration: ButtonStyleConfiguration
    let selected: Bool
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.isFocused) private var isFocused
    @Environment(\.dsFocusContainerHasFocus) private var containerHasFocus
    @State private var hovering = false

    var body: some View {
        let colors = DS.chipColors(selected: selected, enabled: isEnabled, hovering: hovering)
        configuration.label
            .font(DS.font(.label, weight: .medium))
            .lineLimit(1)
            .foregroundStyle(colors.label.color)
            .environment(\.dsChipSelected, selected)
            .padding(.horizontal, 10)
            .frame(minHeight: DS.chipHeight)
            .background {
                Capsule().fill(colors.fill.color)
                    .overlay {
                        if let stroke = colors.stroke {
                            Capsule().strokeBorder(stroke.color, lineWidth: 0.5)
                        }
                    }
            }
            .overlay {
                if configuration.isPressed {
                    Capsule().fill(Color.black.opacity(0.08)).allowsHitTesting(false)
                }
            }
            .contentShape(Capsule())
            .onHover { hovering = $0 }
            .dsFocusRing(isFocused && !containerHasFocus, cornerRadius: DS.chipHeight / 2)
            .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

extension ButtonStyle where Self == DSChipStyle {
    /// A filter or toggle chip, selected or not.
    static func dsChip(selected: Bool) -> DSChipStyle { DSChipStyle(selected: selected) }
}

extension EnvironmentValues {
    /// Whether the enclosing `dsChip` is selected, so its count can match.
    @Entry var dsChipSelected: Bool = false
}

/// The count inside a filter chip, coloured for the chip's state.
struct DSChipCount: View {
    let count: Int
    @Environment(\.dsChipSelected) private var selected
    @Environment(\.isEnabled) private var isEnabled

    init(_ count: Int) { self.count = count }

    var body: some View {
        Text("\(count)")
            .font(DS.font(.caption, .mono))
            .foregroundStyle(DS.chipCountColor(selected: selected, enabled: isEnabled).color)
    }
}

// MARK: - Tag

private struct DSTagModifier: ViewModifier {
    var voice: DS.Voice
    var link: Bool
    @Environment(\.isEnabled) private var isEnabled

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: DS.tagCornerRadius)
        content
            .font(DS.font(.caption2, voice, weight: voice == .mono ? .regular : .medium))
            .lineLimit(1)
            .foregroundStyle(DS.tagTextColor(link: link, enabled: isEnabled).color)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background {
                shape.fill(DS.Paper.raised)
                    .overlay(shape.strokeBorder(DS.Rule.hard, lineWidth: 0.5))
            }
            .contentShape(shape)
    }
}

extension View {
    /// The small boxed tag: a source or deep-link chip on a card, a data
    /// label. `.mono` for identifiers and times; `link: true` when clicking
    /// it opens something. Disabled draws the faintest ink (an unresolved
    /// link).
    func dsTag(_ voice: DS.Voice = .sans, link: Bool = false) -> some View {
        modifier(DSTagModifier(voice: voice, link: link))
    }
}

#if DEBUG
#Preview("Chips, pills and tags") {
    VStack(alignment: .leading, spacing: 12) {
        HStack {
            ForEach(Array(DS.PillTone.all.enumerated()), id: \.offset) { _, tone in
                DSPill("\(tone)".prefix(8).description, tone: tone)
            }
        }
        HStack {
            Button { } label: { HStack(spacing: 6) { Text("All"); DSChipCount(12) } }
                .buttonStyle(.dsChip(selected: true))
            Button { } label: { HStack(spacing: 6) { Text("Briefing"); DSChipCount(3) } }
                .buttonStyle(.dsChip(selected: false))
            Button { } label: { Text("Off") }
                .buttonStyle(.dsChip(selected: false)).disabled(true)
        }
        HStack {
            Text("PROJ-1234").dsTag(.mono)
            Text("Open in Linear").dsTag(link: true)
            Text("missing").dsTag(link: true).disabled(true)
        }
    }
    .padding()
}
#endif
