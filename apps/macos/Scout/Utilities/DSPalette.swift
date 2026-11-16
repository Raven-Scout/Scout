import SwiftUI
import AppKit

// The design system's colours, kept as raw sRGB values so they can be checked
// as well as drawn. `DS.Paper`, `DS.Ink`, `DS.Accent`, `DS.Status` and
// `DS.SlotType` all read from `DS.Palette`; `DS.ContrastPairs` lists every
// text-on-surface pairing the app relies on, and `ContrastPairsTests` fails if
// any of them drops below WCAG AA in light, dark or Increase Contrast.

extension DS {

    /// An sRGB colour with alpha, components 0…1.
    nonisolated struct RGB: Equatable, Sendable {
        var r: Double
        var g: Double
        var b: Double
        var a: Double = 1

        init(_ r: Double, _ g: Double, _ b: Double, _ a: Double = 1) {
            self.r = r; self.g = g; self.b = b; self.a = a
        }

        /// WCAG 2.x relative luminance of the opaque colour.
        var relativeLuminance: Double {
            func linear(_ c: Double) -> Double {
                c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4)
            }
            return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
        }

        /// This colour composited over an opaque `background` ("source over").
        func over(_ background: RGB) -> RGB {
            RGB(r * a + background.r * (1 - a),
                g * a + background.g * (1 - a),
                b * a + background.b * (1 - a))
        }

        /// WCAG 2.x contrast ratio, 1…21. A translucent foreground is
        /// composited over the background first.
        static func contrast(_ foreground: RGB, _ background: RGB) -> Double {
            let fg = foreground.a < 1 ? foreground.over(background) : foreground
            let l1 = fg.relativeLuminance, l2 = background.relativeLuminance
            return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
        }

        var nsColor: NSColor { NSColor(srgbRed: r, green: g, blue: b, alpha: a) }
    }

    /// The four appearances a colour resolves for.
    nonisolated enum Appearance: CaseIterable, Sendable, CustomStringConvertible {
        case light, dark, lightIncreasedContrast, darkIncreasedContrast

        var isDark: Bool { self == .dark || self == .darkIncreasedContrast }

        var description: String {
            switch self {
            case .light: return "light"
            case .dark: return "dark"
            case .lightIncreasedContrast: return "light + Increase Contrast"
            case .darkIncreasedContrast: return "dark + Increase Contrast"
            }
        }

        /// Which of the four an `NSAppearance` is. Increase Contrast shows up
        /// either as a high-contrast appearance or only as the system flag,
        /// depending on where the appearance came from, so both count.
        init(_ appearance: NSAppearance,
             increaseContrast: Bool = NSWorkspace.shared.accessibilityDisplayShouldIncreaseContrast) {
            let match = appearance.bestMatch(from: [
                .aqua, .darkAqua, .accessibilityHighContrastAqua, .accessibilityHighContrastDarkAqua,
            ])
            let dark = match == .darkAqua || match == .accessibilityHighContrastDarkAqua
            let increased = increaseContrast
                || match == .accessibilityHighContrastAqua || match == .accessibilityHighContrastDarkAqua
            self.init(isDark: dark, increaseContrast: increased)
        }

        init(isDark: Bool, increaseContrast: Bool) {
            switch (isDark, increaseContrast) {
            case (false, false): self = .light
            case (true, false):  self = .dark
            case (false, true):  self = .lightIncreasedContrast
            case (true, true):   self = .darkIncreasedContrast
            }
        }
    }

    /// A named colour with light and dark values, and optional stronger
    /// values for Increase Contrast (System Settings ▸ Accessibility ▸
    /// Display). Without them, Increase Contrast uses the normal values.
    nonisolated struct Swatch: Sendable {
        let name: String
        let light: RGB
        let dark: RGB
        var lightIncreasedContrast: RGB? = nil
        var darkIncreasedContrast: RGB? = nil

        func resolved(_ appearance: Appearance) -> RGB {
            switch appearance {
            case .light: return light
            case .dark: return dark
            case .lightIncreasedContrast: return lightIncreasedContrast ?? light
            case .darkIncreasedContrast: return darkIncreasedContrast ?? dark
            }
        }

        /// A dynamic SwiftUI colour that follows the window's appearance.
        var color: Color {
            let swatch = self
            return Color(nsColor: NSColor(name: NSColor.Name(name)) { appearance in
                swatch.resolved(Appearance(appearance)).nsColor
            })
        }
    }

    /// Every text, surface and accent colour. Edit values here;
    /// `ContrastPairsTests` checks the pairs in `ContrastPairs` against them.
    nonisolated enum Palette {
        // Surfaces
        static let paperBase   = Swatch(name: "Paper",       light: RGB(0.985, 0.984, 0.975), dark: RGB(0.145, 0.147, 0.160))
        static let paperSunk   = Swatch(name: "PaperSunk",   light: RGB(0.960, 0.958, 0.948), dark: RGB(0.118, 0.120, 0.132))
        static let paperRaised = Swatch(name: "PaperRaised", light: RGB(1.000, 0.999, 0.995), dark: RGB(0.180, 0.182, 0.197))

        /// The two radial tints `PaperBackdrop` lays over `paperBase`. At
        /// their centres they darken the page, so text on the backdrop is
        /// checked against base-plus-tint as well as plain paper.
        static let backdropTintLeading  = Swatch(name: "BackdropTintLeading",
                                                 light: RGB(0.83, 0.81, 0.92, 0.45), dark: RGB(0.20, 0.18, 0.30, 0.45))
        static let backdropTintTrailing = Swatch(name: "BackdropTintTrailing",
                                                 light: RGB(0.95, 0.85, 0.62, 0.40), dark: RGB(0.25, 0.18, 0.10, 0.40))

        // Text. Before C7, p3 was ~3.7:1 and p4 ~2.3:1 on light paper.
        static let inkP1 = Swatch(name: "InkP1", light: RGB(0.125, 0.130, 0.195), dark: RGB(0.950, 0.948, 0.940))
        static let inkP2 = Swatch(name: "InkP2", light: RGB(0.310, 0.315, 0.380), dark: RGB(0.780, 0.775, 0.760),
                                  lightIncreasedContrast: RGB(0.200, 0.205, 0.265), darkIncreasedContrast: RGB(0.880, 0.878, 0.870))
        static let inkP3 = Swatch(name: "InkP3", light: RGB(0.372, 0.372, 0.415), dark: RGB(0.665, 0.663, 0.650),
                                  lightIncreasedContrast: RGB(0.250, 0.255, 0.310), darkIncreasedContrast: RGB(0.820, 0.818, 0.808))
        static let inkP4 = Swatch(name: "InkP4", light: RGB(0.398, 0.398, 0.425), dark: RGB(0.600, 0.598, 0.588),
                                  lightIncreasedContrast: RGB(0.300, 0.305, 0.360), darkIncreasedContrast: RGB(0.760, 0.758, 0.748))
        /// Dark text for labels on a mid-tone tint (slot-type pills, weekday
        /// dots); white on those tints is 2–4:1.
        static let inkOnTint = Swatch(name: "InkOnTint", light: RGB(0.100, 0.080, 0.040), dark: RGB(0.100, 0.080, 0.040))

        // Rules (non-text; stronger under Increase Contrast)
        static let ruleSoft = Swatch(name: "RuleSoft", light: RGB(0.910, 0.905, 0.890), dark: RGB(0.240, 0.244, 0.260),
                                     lightIncreasedContrast: RGB(0.700, 0.700, 0.690), darkIncreasedContrast: RGB(0.480, 0.484, 0.500))
        static let ruleHard = Swatch(name: "RuleHard", light: RGB(0.870, 0.865, 0.848), dark: RGB(0.300, 0.305, 0.325),
                                     lightIncreasedContrast: RGB(0.600, 0.600, 0.590), darkIncreasedContrast: RGB(0.560, 0.565, 0.585))

        // Accent
        static let accentFill   = Swatch(name: "AccentFill",   light: RGB(0.745, 0.540, 0.240), dark: RGB(0.915, 0.790, 0.470))
        static let accentInk    = Swatch(name: "AccentInk",    light: RGB(0.535, 0.365, 0.112), dark: RGB(0.940, 0.840, 0.560))
        static let accentWash   = Swatch(name: "AccentWash",   light: RGB(0.975, 0.945, 0.880), dark: RGB(0.310, 0.250, 0.140))
        /// Text on `accentFill`. White there was ~3:1 (light) and ~1.6:1 (dark).
        static let accentOnFill = Swatch(name: "AccentOnFill", light: RGB(0.120, 0.090, 0.030), dark: RGB(0.145, 0.147, 0.160))

        // Status (text-safe on paper)
        static let statusOk   = Swatch(name: "StatusOk",   light: RGB(0.225, 0.440, 0.292), dark: RGB(0.430, 0.720, 0.500))
        static let statusWarn = Swatch(name: "StatusWarn", light: RGB(0.520, 0.368, 0.130), dark: RGB(0.880, 0.660, 0.300))
        static let statusErr  = Swatch(name: "StatusErr",  light: RGB(0.690, 0.255, 0.180), dark: RGB(0.895, 0.480, 0.410))

        // Slot-type hues (fills; their labels use `inkOnTint`)
        static let slotBriefing      = Swatch(name: "SlotBriefing",      light: RGB(0.860, 0.660, 0.180), dark: RGB(0.910, 0.760, 0.340))
        static let slotConsolidation = Swatch(name: "SlotConsolidation", light: RGB(0.400, 0.580, 0.760), dark: RGB(0.520, 0.700, 0.870))
        static let slotDreaming      = Swatch(name: "SlotDreaming",      light: RGB(0.560, 0.460, 0.760), dark: RGB(0.700, 0.620, 0.880))
        static let slotResearch      = Swatch(name: "SlotResearch",      light: RGB(0.420, 0.620, 0.420), dark: RGB(0.520, 0.740, 0.540))
        static let slotManual        = Swatch(name: "SlotManual",        light: RGB(0.620, 0.620, 0.640), dark: RGB(0.520, 0.520, 0.535))
    }

    /// The text-on-surface pairings the app draws, with the minimum ratio
    /// each must meet. WCAG AA asks 4.5:1 for text under 18 pt (14 pt bold);
    /// almost all of Scout's text is 9–13 pt, so every pair uses 4.5.
    nonisolated enum ContrastPairs {
        nonisolated struct Pair: Sendable, CustomStringConvertible {
            let foreground: Swatch
            let background: Surface
            var minimum: Double = 4.5
            var description: String { "\(foreground.name) on \(background.name)" }

            func ratio(_ appearance: Appearance) -> Double {
                RGB.contrast(foreground.resolved(appearance), background.resolved(appearance))
            }
        }

        /// A background: a plain swatch, or a translucent swatch composited
        /// over another one (the backdrop tints over paper).
        nonisolated struct Surface: Sendable {
            let name: String
            let swatch: Swatch
            var under: Swatch? = nil

            func resolved(_ appearance: Appearance) -> RGB {
                let top = swatch.resolved(appearance)
                guard let under else { return top }
                return top.over(under.resolved(appearance))
            }
        }

        /// Every surface body text sits on.
        static let paperSurfaces: [Surface] = [
            Surface(name: "Paper.base", swatch: Palette.paperBase),
            Surface(name: "Paper.sunk", swatch: Palette.paperSunk),
            Surface(name: "Paper.raised", swatch: Palette.paperRaised),
            Surface(name: "backdrop (leading tint)", swatch: Palette.backdropTintLeading, under: Palette.paperBase),
            Surface(name: "backdrop (trailing tint)", swatch: Palette.backdropTintTrailing, under: Palette.paperBase),
        ]

        /// Swatches used as text on paper.
        static let paperText: [Swatch] = [
            Palette.inkP1, Palette.inkP2, Palette.inkP3, Palette.inkP4,
            Palette.accentInk, Palette.statusOk, Palette.statusWarn, Palette.statusErr,
        ]

        static let all: [Pair] = {
            var pairs = paperText.flatMap { text in
                paperSurfaces.map { Pair(foreground: text, background: $0) }
            }
            // Labels on the accent (badges, primary buttons).
            pairs.append(Pair(foreground: Palette.accentOnFill,
                              background: Surface(name: "Accent.fill", swatch: Palette.accentFill)))
            // The selected segment of `EditorialSegmentedControl`: paper on ink.
            pairs.append(Pair(foreground: Palette.paperBase,
                              background: Surface(name: "Ink.p1", swatch: Palette.inkP1)))
            // Slot-type pills and weekday dots.
            for slot in [Palette.slotBriefing, Palette.slotConsolidation, Palette.slotDreaming,
                         Palette.slotResearch, Palette.slotManual] {
                pairs.append(Pair(foreground: Palette.inkOnTint,
                                  background: Surface(name: slot.name, swatch: slot)))
            }
            return pairs
        }()
    }
}
