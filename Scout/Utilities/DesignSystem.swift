import SwiftUI
import AppKit

/// Editorial + native-macOS design system.
///
/// Source of truth for the Scout refresh: warm paper + deep ink surfaces,
/// muted accent, priority hues at matched chroma, and a typographic system
/// that mirrors the handoff bundle's Newsreader / JetBrains Mono / SF stack.
enum DS {

    // MARK: - Semantic palette

    /// Paper is the page. Sunk is a recessed well. Raised is a lifted card.
    enum Paper {
        static let base   = Color("Paper",       bundle: nil, fallbackLight: .sRGB(0.985, 0.984, 0.975, 1), fallbackDark: .sRGB(0.145, 0.147, 0.160, 1))
        static let sunk   = Color("PaperSunk",   bundle: nil, fallbackLight: .sRGB(0.960, 0.958, 0.948, 1), fallbackDark: .sRGB(0.118, 0.120, 0.132, 1))
        static let raised = Color("PaperRaised", bundle: nil, fallbackLight: .sRGB(1.000, 0.999, 0.995, 1), fallbackDark: .sRGB(0.180, 0.182, 0.197, 1))
    }

    /// Ink is the foreground type. 1 is primary, 4 is the faintest UI hint.
    enum Ink {
        static let p1 = Color(fallbackLight: .sRGB(0.125, 0.130, 0.195, 1), fallbackDark: .sRGB(0.950, 0.948, 0.940, 1))
        static let p2 = Color(fallbackLight: .sRGB(0.310, 0.315, 0.380, 1), fallbackDark: .sRGB(0.780, 0.775, 0.760, 1))
        static let p3 = Color(fallbackLight: .sRGB(0.510, 0.510, 0.565, 1), fallbackDark: .sRGB(0.600, 0.598, 0.585, 1))
        static let p4 = Color(fallbackLight: .sRGB(0.660, 0.660, 0.700, 1), fallbackDark: .sRGB(0.440, 0.438, 0.425, 1))
    }

    /// Hairline rules.
    enum Rule {
        static let soft = Color(fallbackLight: .sRGB(0.91, 0.905, 0.890, 1), fallbackDark: .sRGB(0.240, 0.244, 0.260, 1))
        static let hard = Color(fallbackLight: .sRGB(0.87, 0.865, 0.848, 1), fallbackDark: .sRGB(0.300, 0.305, 0.325, 1))
    }

    /// Editorial amber accent. `ink` is the readable-on-paper variant.
    enum Accent {
        static let fill = Color(fallbackLight: .sRGB(0.745, 0.540, 0.240, 1), fallbackDark: .sRGB(0.915, 0.790, 0.470, 1))
        static let ink  = Color(fallbackLight: .sRGB(0.555, 0.380, 0.120, 1), fallbackDark: .sRGB(0.940, 0.840, 0.560, 1))
        static let wash = Color(fallbackLight: .sRGB(0.975, 0.945, 0.880, 1), fallbackDark: .sRGB(0.310, 0.250, 0.140, 1))
    }

    /// Priority hues sit in the same chroma family on purpose — they read as a
    /// set, not a stoplight.
    enum Priority {
        static let urgent   = Color(fallbackLight: .sRGB(0.750, 0.310, 0.200, 1), fallbackDark: .sRGB(0.880, 0.450, 0.340, 1))
        static let todo     = Color(fallbackLight: .sRGB(0.840, 0.640, 0.170, 1), fallbackDark: .sRGB(0.910, 0.760, 0.340, 1))
        static let watch    = Color(fallbackLight: .sRGB(0.390, 0.600, 0.390, 1), fallbackDark: .sRGB(0.500, 0.740, 0.520, 1))
        static let personal = Color(fallbackLight: .sRGB(0.540, 0.440, 0.740, 1), fallbackDark: .sRGB(0.700, 0.620, 0.880, 1))
        static let done     = Color(fallbackLight: .sRGB(0.620, 0.620, 0.640, 1), fallbackDark: .sRGB(0.520, 0.520, 0.535, 1))
    }

    /// Status feedback colors (meeting-now, success, warn, error).
    enum Status {
        static let ok   = Color(fallbackLight: .sRGB(0.300, 0.580, 0.385, 1), fallbackDark: .sRGB(0.430, 0.720, 0.500, 1))
        static let warn = Color(fallbackLight: .sRGB(0.720, 0.510, 0.180, 1), fallbackDark: .sRGB(0.880, 0.660, 0.300, 1))
        static let err  = Color(fallbackLight: .sRGB(0.750, 0.280, 0.200, 1), fallbackDark: .sRGB(0.880, 0.420, 0.340, 1))
    }

    // MARK: - Typography

    /// Reading voice. Falls back to Apple's "New York" serif on macOS.
    static func serif(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        if NSFont(name: "Newsreader", size: size) != nil {
            return .custom("Newsreader", size: size).weight(weight)
        }
        if NSFont(name: "New York", size: size) != nil {
            return .custom("New York", size: size).weight(weight)
        }
        return .system(size: size, weight: weight, design: .serif)
    }

    /// IDs, timestamps, key counts.
    static func mono(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        if NSFont(name: "JetBrains Mono", size: size) != nil {
            return .custom("JetBrains Mono", size: size).weight(weight)
        }
        return .system(size: size, weight: weight, design: .monospaced)
    }

    /// UI chrome (buttons, labels, filters). SF Pro on macOS.
    static func sans(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .default)
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

    /// Small glyph shown in section headers (matches the handoff bundle).
    static func kindGlyph(_ kind: ActionSection.Kind) -> String {
        switch kind {
        case .urgent:   return "🔴"
        case .todo:     return "🟡"
        case .watching: return "🟢"
        case .personal: return "🏡"
        case .focus:    return "💡"
        case .meetings: return "📅"
        case .done:     return "✓"
        case .digest:   return "📋"
        case .neutral:  return "·"
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

// MARK: - Color bridging

private extension Color {
    /// Light/dark sRGB fallback pair — renders the editorial palette without
    /// requiring asset-catalog entries.
    init(fallbackLight: NSColor, fallbackDark: NSColor) {
        self = Color(nsColor: NSColor(name: nil) { appearance in
            let dark = appearance.bestMatch(from: [.darkAqua, .vibrantDark, .accessibilityHighContrastDarkAqua]) != nil
            return dark ? fallbackDark : fallbackLight
        })
    }

    /// Optional named asset with sRGB fallback for machines without the
    /// asset catalog updated yet.
    init(_ name: String, bundle: Bundle?, fallbackLight: NSColor, fallbackDark: NSColor) {
        if NSColor(named: NSColor.Name(name), bundle: bundle) != nil {
            self = Color(nsColor: NSColor(named: NSColor.Name(name), bundle: bundle)!)
        } else {
            self.init(fallbackLight: fallbackLight, fallbackDark: fallbackDark)
        }
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
                colors: [
                    Color(fallbackLight: .sRGB(0.83, 0.81, 0.92, 0.45),
                          fallbackDark:  .sRGB(0.20, 0.18, 0.30, 0.45)),
                    .clear
                ],
                center: .topLeading,
                startRadius: 0,
                endRadius: 760
            )
            RadialGradient(
                colors: [
                    Color(fallbackLight: .sRGB(0.95, 0.85, 0.62, 0.40),
                          fallbackDark:  .sRGB(0.25, 0.18, 0.10, 0.40)),
                    .clear
                ],
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

/// The small boxed chip/pill used for deep-links, tags, and action buttons.
struct EditorialChipBackground: View {
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 5).fill(DS.Paper.raised)
            RoundedRectangle(cornerRadius: 5).strokeBorder(DS.Rule.hard, lineWidth: 0.5)
        }
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
        static let briefing      = Color(fallbackLight: .sRGB(0.860, 0.660, 0.180, 1),
                                         fallbackDark:  .sRGB(0.910, 0.760, 0.340, 1))
        /// Consolidation — desaturated steel blue.
        static let consolidation = Color(fallbackLight: .sRGB(0.400, 0.580, 0.760, 1),
                                         fallbackDark:  .sRGB(0.520, 0.700, 0.870, 1))
        /// Dreaming — quiet violet.
        static let dreaming      = Color(fallbackLight: .sRGB(0.560, 0.460, 0.760, 1),
                                         fallbackDark:  .sRGB(0.700, 0.620, 0.880, 1))
        /// Research — sage green.
        static let research      = Color(fallbackLight: .sRGB(0.420, 0.620, 0.420, 1),
                                         fallbackDark:  .sRGB(0.520, 0.740, 0.540, 1))
        /// Manual — matched-chroma neutral; manual slots have no fixed
        /// cadence and shouldn't compete visually with the four colored types.
        static let manual        = Color(fallbackLight: .sRGB(0.620, 0.620, 0.640, 1),
                                         fallbackDark:  .sRGB(0.520, 0.520, 0.535, 1))

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
