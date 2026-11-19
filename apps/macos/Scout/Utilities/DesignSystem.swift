import SwiftUI
import AppKit
import CoreText

/// Editorial + native-macOS design system.
///
/// Source of truth for the Scout refresh: warm paper + deep ink surfaces,
/// muted accent, priority hues at matched chroma, and a typographic system
/// that mirrors the handoff bundle's Newsreader / JetBrains Mono / SF stack.
enum DS {

    // MARK: - Semantic palette
    //
    // Every colour below is a `DS.Palette` token, so its raw light/dark values
    // can be read back by `ContrastTokenTests`, which checks each text/surface
    // pair the app uses against WCAG AA (4.5:1). Change a value here and that
    // test tells you whether it is still readable — see `DS.ContrastPairs`.

    /// Paper is the page. Sunk is a recessed well. Raised is a lifted card.
    enum Paper {
        static let base   = Palette.paperBase.color
        static let sunk   = Palette.paperSunk.color
        static let raised = Palette.paperRaised.color
    }

    /// Ink is the foreground type. 1 is primary, 4 is the faintest UI hint.
    /// All four meet WCAG AA (4.5:1) as text on every paper surface, the
    /// backdrop tint included, in light and dark; with Increase Contrast on,
    /// 3 and 4 darken (lighten in dark mode) further.
    enum Ink {
        static let p1 = Palette.inkP1.color
        static let p2 = Palette.inkP2.color
        static let p3 = Palette.inkP3.color
        static let p4 = Palette.inkP4.color
        /// Labels drawn on a mid-tone tint (slot-type pills, weekday dots).
        static let onTint = Palette.inkOnTint.color
        /// Strokes, not text: strikethroughs, outlines, ticks.
        static let decorative = Palette.inkDecorative.color
    }

    /// Hairline rules.
    enum Rule {
        static let soft = Palette.ruleSoft.color
        static let hard = Palette.ruleHard.color
    }

    /// Editorial amber accent. `ink` is the readable-on-paper variant.
    /// `onFill` is the text colour for labels drawn *on* `fill` (badges,
    /// primary buttons): white on the amber is ~3:1 in light mode and ~1.6:1
    /// in dark mode, so labels on the accent use a dark ink in both modes.
    enum Accent {
        static let fill   = Palette.accentFill.color
        static let ink    = Palette.accentInk.color
        static let wash   = Palette.accentWash.color
        static let onFill = Palette.accentOnFill.color
    }

    /// Priority hues sit in the same chroma family on purpose — they read as a
    /// set, not a stoplight.
    enum Priority {
        static let urgent   = Palette.priorityUrgent.color
        static let todo     = Color(fallbackLight: .sRGB(0.840, 0.640, 0.170, 1), fallbackDark: .sRGB(0.910, 0.760, 0.340, 1))
        static let watch    = Color(fallbackLight: .sRGB(0.390, 0.600, 0.390, 1), fallbackDark: .sRGB(0.500, 0.740, 0.520, 1))
        static let personal = Color(fallbackLight: .sRGB(0.540, 0.440, 0.740, 1), fallbackDark: .sRGB(0.700, 0.620, 0.880, 1))
        static let done     = Color(fallbackLight: .sRGB(0.620, 0.620, 0.640, 1), fallbackDark: .sRGB(0.520, 0.520, 0.535, 1))
    }

    /// Status feedback colors (meeting-now, success, warn, error). Text-safe:
    /// each meets 4.5:1 on every paper surface, so a "Healthy" or "Needs
    /// attention" label can use them directly.
    enum Status {
        static let ok   = Palette.statusOk.color
        static let warn = Palette.statusWarn.color
        static let err  = Palette.statusErr.color
        /// Text on a solid `err` (or other status) fill.
        static let onFill = Palette.statusOnFill.color
        /// Opacity of a status wash behind status-coloured text.
        static let washOpacity = ContrastPairs.statusWashOpacity
    }

    /// Workflow states that mean "this is waiting on you": a topic-board item
    /// that is yours ("Mine") and a session that needs you. Kept apart from
    /// `Priority.urgent` so the urgent red means only Urgent (audit #107).
    enum Attention {
        static let you = Palette.attentionYou.color
    }

    // MARK: - Typography
    //
    // macOS has no Dynamic Type: `.dynamicTypeSize` and `Font.TextStyle` sizes
    // don't change with any system setting (checked on macOS 26 — a `.body`
    // Text measures the same at every `DynamicTypeSize`). So every size the
    // app draws goes through ONE knob instead: `TextScale.shared`, set in
    // Settings ▸ General ▸ Text size.
    //
    // Views pick a role, never a point size (design-system v2, audit #104):
    // `DS.font(.caption)`, `DS.font(.body, .serif)`, `DS.font(.label, .mono)`.
    // The roles live in `TextStyle` below. The sized `serif`/`mono`/`sans`
    // functions are the scale's own building blocks, and `TypeRoleGuardTests`
    // fails on a call to them from anywhere else. An SF Symbol drawn as a
    // picture (an empty-state glyph) takes `DS.symbol(_:)` instead.

    /// Reading voice. Falls back to Apple's "New York" serif on macOS.
    /// Views call `DS.font(_:.serif)`; this is the scale's building block.
    static func serif(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        let size = scaled(size)
        if BrandFont.available {
            return .custom(BrandFont.newsreader(weight), size: size)
        }
        if FontAvailability.newYork {
            return .custom("New York", size: size).weight(weight)
        }
        return .system(size: size, weight: weight, design: .serif)
    }

    /// IDs, timestamps, key counts. Views call `DS.font(_:.mono)`.
    static func mono(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        let size = scaled(size)
        if BrandFont.available {
            return .custom(BrandFont.jetBrainsMono(weight), size: size)
        }
        return .system(size: size, weight: weight, design: .monospaced)
    }

    /// UI chrome (buttons, labels, filters). SF Pro on macOS. Views call
    /// `DS.font(_:)`.
    static func sans(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: scaled(size), weight: weight, design: .default)
    }

    /// A glyph drawn as a picture at a fixed design size: an empty-state
    /// glyph, or a symbol sized to the shape around it (the day-circle
    /// strip). Text never uses this; it takes a role through `DS.font`.
    /// It still follows the Text size setting.
    static func symbol(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        sans(size, weight: weight)
    }

    /// A design point size at the user's chosen text size. Reading
    /// `TextScale.shared` inside a view's `body` registers an Observation
    /// dependency, so changing the setting re-renders every view that sized
    /// text through `DS` — no relaunch, no view-identity reset.
    static func scaled(_ size: CGFloat) -> CGFloat {
        scaledSize(size, factor: TextScale.shared.factor)
    }

    /// Pure scaling rule: multiply, then round to the nearest half point so
    /// glyphs stay crisp. `factor` 1 returns `size` unchanged.
    nonisolated static func scaledSize(_ size: CGFloat, factor: CGFloat) -> CGFloat {
        guard factor != 1 else { return size }
        return (size * factor * 2).rounded() / 2
    }

    /// The semantic type scale. Views pick a role, not a point size; the
    /// scale maps each role to one design size, and `TextScale` grows them
    /// all together. Nine sizes cover the whole app: the 26 sizes views used
    /// before design-system v2 snapped to the nearest role.
    enum TextStyle: CaseIterable, Sendable {
        /// The smallest type: count badges, chevrons and keycaps inside a
        /// control, timeline tick labels.
        case micro
        /// Small uppercase group labels, badges, dense metadata.
        case caption2
        /// Help text, secondary lines, timestamps.
        case caption
        /// Control labels: buttons, chips, segmented controls.
        case label
        /// Running text and list rows.
        case body
        /// Card and section titles.
        case headline
        /// Page section titles.
        case title
        /// A large figure or the title of a sheet or window that isn't a
        /// page: a usage total, the onboarding and glossary titles.
        case hero
        /// Page titles.
        case display

        /// Design size in points at the standard text size.
        nonisolated var pointSize: CGFloat {
            switch self {
            case .micro:    return 9
            case .caption2: return 10.5
            case .caption:  return 11.5
            case .label:    return 12.5
            case .body:     return 13
            case .headline: return 15
            case .title:    return 18
            case .hero:     return 22
            case .display:  return 28
            }
        }
    }

    /// Which typeface family carries the text.
    enum Voice: Sendable {
        /// SF Pro — UI chrome.
        case sans
        /// Newsreader / New York — reading voice.
        case serif
        /// JetBrains Mono / SF Mono — data and identifiers only.
        case mono
    }

    /// Extra leading for the reading voice, in ems. Newsreader's line height
    /// is 1.00 em where New York's is 1.19, so serif text set with it alone
    /// loses about 16% of its leading and ascenders can touch the line above.
    /// `dsFont(_:.serif)` adds the difference back. Mono needs none: JetBrains
    /// Mono's 1.32 em is already taller than SF Mono's.
    nonisolated static let serifExtraLeading: CGFloat = 0.19

    /// The line spacing `dsFont` adds for a role and voice.
    static func extraLeading(_ style: TextStyle, _ voice: Voice) -> CGFloat {
        guard voice == .serif, BrandFont.available else { return 0 }
        return (scaled(style.pointSize) * serifExtraLeading).rounded()
    }

    /// The semantic font for a role. Prefer this over a raw `sans(_:)` size.
    /// Serif text takes `View.dsFont(_:.serif)`, which adds its leading.
    static func font(_ style: TextStyle, _ voice: Voice = .sans, weight: Font.Weight = .regular) -> Font {
        switch voice {
        case .sans:  return sans(style.pointSize, weight: weight)
        case .serif: return serif(style.pointSize, weight: weight)
        case .mono:  return mono(style.pointSize, weight: weight)
        }
    }

    /// The brand fonts ship inside the app (audit #106): Newsreader's 16 pt
    /// optical cut for the reading voice and JetBrains Mono for data, each in
    /// the three weights the app draws (Regular, Medium, SemiBold), plus
    /// Newsreader's matching italics so `.italic()` and markdown emphasis in
    /// serif text get the drawn italic, not a slanted roman. Their SIL Open
    /// Font License texts sit beside them in Resources/Fonts.
    /// Nothing is fetched at run time; they are registered for this process
    /// only, the first time a serif or mono font is built.
    ///
    /// Faces are picked by PostScript name rather than `.weight(_:)`, so a
    /// weight the app doesn't bundle maps to the nearest real face instead of
    /// a synthesised one.
    enum BrandFont {
        static let newsreaderFaces = ["Newsreader16pt-Regular", "Newsreader16pt-Medium", "Newsreader16pt-SemiBold"]
        static let jetBrainsMonoFaces = ["JetBrainsMono-Regular", "JetBrainsMono-Medium", "JetBrainsMono-SemiBold"]
        /// Not picked by name: CoreText finds them through the family when a
        /// view asks for italic.
        static let newsreaderItalicFaces = ["Newsreader16pt-Italic", "Newsreader16pt-MediumItalic", "Newsreader16pt-SemiBoldItalic"]
        static var allFaces: [String] { newsreaderFaces + newsreaderItalicFaces + jetBrainsMonoFaces }

        /// Registers the bundled files once, then checks every face loaded.
        /// False only if the bundle lost them; the voices then fall back to
        /// New York and SF Mono.
        static let available: Bool = {
            registerBundledFonts()
            return allFaces.allSatisfy { NSFont(name: $0, size: 13) != nil }
        }()

        static func newsreader(_ weight: Font.Weight) -> String { newsreaderFaces[index(weight)] }

        /// The bundled face AppKit text (the KB editor) uses for bold: the
        /// SemiBold of the same family and slant. Nil for a face that isn't
        /// a bundled brand face.
        static func boldFace(of name: String) -> String? {
            if newsreaderItalicFaces.contains(name) { return newsreaderItalicFaces[2] }
            if newsreaderFaces.contains(name) { return newsreaderFaces[2] }
            if jetBrainsMonoFaces.contains(name) { return jetBrainsMonoFaces[2] }
            return nil
        }
        static func jetBrainsMono(_ weight: Font.Weight) -> String { jetBrainsMonoFaces[index(weight)] }

        /// Regular for regular and lighter, Medium for medium, SemiBold for
        /// semibold and heavier.
        static func index(_ weight: Font.Weight) -> Int {
            switch weight {
            case .medium: return 1
            case .semibold, .bold, .heavy, .black: return 2
            default: return 0
            }
        }

        private static func registerBundledFonts() {
            guard let resources = Bundle.main.resourceURL,
                  let files = FileManager.default.enumerator(at: resources, includingPropertiesForKeys: nil)
            else { return }
            for case let url as URL in files where url.pathExtension == "ttf" {
                // Already registered (a second app instance in tests) is fine.
                CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
            }
        }
    }

    /// The fallback serif is a system install; look it up once, not on every
    /// font call (the old per-call `NSFont(name:)` probe ran on every render).
    private enum FontAvailability {
        static let newYork       = NSFont(name: "New York", size: 13) != nil
    }

    // MARK: - Kind → priority mapping

    /// Map a section kind to its priority dot hue. Keeps the existing
    /// `ActionSection.Kind` API intact.
    static func priorityColor(_ kind: ActionSection.Kind) -> Color {
        switch kind {
        case .urgent:   return Priority.urgent
        case .todo:     return Priority.todo
        case .watching: return Priority.watch
        case .personal: return Priority.personal
        case .done:     return Priority.done
        case .focus:    return Accent.fill
        case .meetings: return Accent.fill
        case .digest:   return Ink.p3
        case .neutral:  return Ink.p4
        }
    }

    /// SF Symbol shown inside a `KindMarker` for category kinds. Returns nil
    /// for kinds that render as a plain colored dot (the priority axis + neutral).
    static func kindSymbol(_ kind: ActionSection.Kind) -> String? {
        switch kind {
        case .urgent, .todo, .watching, .neutral: return nil
        case .done:     return "checkmark"
        case .personal: return "house"
        case .focus:    return "lightbulb"
        case .meetings: return "calendar"
        case .digest:   return "list.clipboard"
        }
    }
}

// MARK: - Text in a role

extension View {
    /// Text in a design-system role and voice: `DS.font` plus the voice's
    /// leading (`DS.extraLeading`). Required for the serif voice, whose
    /// bundled face is set tighter than the system serif it replaced;
    /// `TypeRoleGuardTests` flags `.font(DS.font(…, .serif))`.
    func dsFont(_ style: DS.TextStyle, _ voice: DS.Voice = .sans, weight: Font.Weight = .regular) -> some View {
        font(DS.font(style, voice, weight: weight))
            .lineSpacing(DS.extraLeading(style, voice))
    }
}

// MARK: - Color bridging

private extension Color {
    /// Light/dark sRGB pair for decorative colours (shadows, backdrop tints,
    /// slot hues) that aren't text tokens. Text and surface colours live in
    /// `DS.Palette` so the contrast test can read them.
    init(fallbackLight: NSColor, fallbackDark: NSColor) {
        self = Color(nsColor: NSColor(name: nil) { appearance in
            DS.Appearance(appearance).isDark ? fallbackDark : fallbackLight
        })
    }
}

private extension NSColor {
    static func sRGB(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat) -> NSColor {
        NSColor(srgbRed: r, green: g, blue: b, alpha: a)
    }
}

// MARK: - Neumorphic shadow recipes

extension DS {
    /// Dual-direction soft-extrude shadow tones. Top-left highlight + bottom-right
    /// shadow, on the warm paper base. Matches the handoff bundle's --nm-hi /
    /// --nm-sh CSS tokens (see Scout.html design tokens).
    enum Neumorphic {
        static let shadow   = Color(fallbackLight: .sRGB(0.510, 0.430, 0.330, 0.22),
                                    fallbackDark:  .sRGB(0.000, 0.000, 0.000, 0.55))
        static let shadow2  = Color(fallbackLight: .sRGB(0.510, 0.430, 0.330, 0.14),
                                    fallbackDark:  .sRGB(0.000, 0.000, 0.000, 0.35))
        static let highlight = Color(fallbackLight: .sRGB(1.000, 0.988, 0.961, 0.95),
                                     fallbackDark:  .sRGB(1.000, 0.980, 0.940, 0.04))
    }
}

/// Soft extruded surface — top-left highlight + bottom-right shadow. Used for
/// raised buttons, chips, pdots, and cards.
struct NeumorphicRaised: ViewModifier {
    var cornerRadius: CGFloat = 8
    var small: Bool = false

    func body(content: Content) -> some View {
        let shadowRadius: CGFloat   = small ? 5 : 10
        let highlightRadius: CGFloat = small ? 3 : 8
        let offset: CGFloat         = small ? 2 : 4
        return content
            .background(
                RoundedRectangle(cornerRadius: cornerRadius)
                    .fill(DS.Paper.base)
                    .shadow(color: DS.Neumorphic.shadow, radius: shadowRadius, x: offset, y: offset)
                    .shadow(color: DS.Neumorphic.highlight, radius: highlightRadius, x: -offset, y: -offset)
            )
    }
}

/// Inset/pressed surface — used for trays, segment backgrounds, fields, and
/// the active state on chips and sidebar items. SwiftUI can't do true inset
/// shadows, so this approximates it with a darker recessed fill + hairline.
struct NeumorphicPressed: ViewModifier {
    var cornerRadius: CGFloat = 8

    func body(content: Content) -> some View {
        content
            .background(
                RoundedRectangle(cornerRadius: cornerRadius)
                    .fill(DS.Paper.sunk)
                    .overlay(
                        RoundedRectangle(cornerRadius: cornerRadius)
                            .strokeBorder(DS.Neumorphic.shadow2, lineWidth: 0.5)
                    )
            )
    }
}

extension View {
    /// Apply a soft extruded surface — used for raised chrome on the warm
    /// paper canvas.
    func neumorphicRaised(cornerRadius: CGFloat = 8, small: Bool = false) -> some View {
        modifier(NeumorphicRaised(cornerRadius: cornerRadius, small: small))
    }

    /// Apply a recessed surface — used for trays, inputs, and active segmented
    /// chrome (the "pushed-in" state).
    func neumorphicPressed(cornerRadius: CGFloat = 8) -> some View {
        modifier(NeumorphicPressed(cornerRadius: cornerRadius))
    }
}

/// The warm radial canvas backdrop. Two soft tinted ellipses fade into the
/// base paper — gives the window depth without competing with foreground type.
struct PaperBackdrop: View {
    var body: some View {
        ZStack {
            DS.Paper.base
            RadialGradient(
                colors: [DS.Palette.backdropTintLeading.color, .clear],
                center: .topLeading,
                startRadius: 0,
                endRadius: 760
            )
            RadialGradient(
                colors: [DS.Palette.backdropTintTrailing.color, .clear],
                center: .bottomTrailing,
                startRadius: 0,
                endRadius: 680
            )
        }
        .ignoresSafeArea()
    }
}

// MARK: - Shared surfaces

/// Editorial card chrome. Uses the raised paper fill with a hairline rule —
/// no drop shadows on heavy scroll-containers (they eat scroll perf). Pass
/// `neumorphic: true` for the soft extruded variant used in hero/dashboard
/// surfaces where shadow cost is acceptable.
struct EditorialCard: ViewModifier {
    var padding: CGFloat = 16
    var cornerRadius: CGFloat = 8
    var neumorphic: Bool = false

    func body(content: Content) -> some View {
        let base = content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
        if neumorphic {
            return AnyView(
                base
                    .background(RoundedRectangle(cornerRadius: cornerRadius).fill(DS.Paper.raised))
                    .neumorphicRaised(cornerRadius: cornerRadius, small: true)
            )
        }
        return AnyView(
            base
                .background(RoundedRectangle(cornerRadius: cornerRadius).fill(DS.Paper.raised))
                .overlay(RoundedRectangle(cornerRadius: cornerRadius).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
    }
}

extension View {
    func editorialCard(padding: CGFloat = 16, cornerRadius: CGFloat = 8, neumorphic: Bool = false) -> some View {
        modifier(EditorialCard(padding: padding, cornerRadius: cornerRadius, neumorphic: neumorphic))
    }
}

/// A single hairline rule, full width, for separating editorial sections.
struct EditorialRule: View {
    var color: Color = DS.Rule.soft
    var body: some View {
        Rectangle().fill(color).frame(height: 0.5)
    }
}

// MARK: - Slot-type color palette

extension DS {
    /// Slot-type color palette. Distinct from `DS.Accent.fill` (orange `+ New`)
    /// and from `DS.Priority.*` (action-item urgency axis).
    enum SlotType {
        /// Briefing — warm amber.
        static let briefing      = Palette.slotBriefing.color
        /// Consolidation — desaturated steel blue.
        static let consolidation = Palette.slotConsolidation.color
        /// Dreaming — quiet violet.
        static let dreaming      = Palette.slotDreaming.color
        /// Research — sage green.
        static let research      = Palette.slotResearch.color
        /// Manual — matched-chroma neutral; manual slots have no fixed
        /// cadence and shouldn't compete visually with the four colored types.
        static let manual        = Palette.slotManual.color

        /// Convenience lookup. Used by every cell that renders a type-tinted dot.
        static func color(for type: Scout.SlotType) -> Color {
            switch type {
            case .briefing:      return briefing
            case .consolidation: return consolidation
            case .dreaming:      return dreaming
            case .research:      return research
            case .manual:        return manual
            }
        }
    }
}
