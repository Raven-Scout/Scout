import AppKit
import SwiftUI
import Testing
@testable import Scout

/// WCAG AA for the design system's colours (C7). `DS.ContrastPairs.all` lists
/// every text-on-surface pairing the app draws; each must reach its minimum
/// (4.5:1) in light, dark, and both Increase Contrast appearances. If you
/// change a value in `DS.Palette` and this fails, the new colour is not
/// readable — pick a darker (light mode) or lighter (dark mode) one.
@Suite("Design-system contrast")
struct ContrastPairsTests {

    @Test("every text/surface pair meets WCAG AA in every appearance",
          arguments: DS.Appearance.allCases)
    func everyPairMeetsAA(_ appearance: DS.Appearance) {
        for pair in DS.ContrastPairs.all {
            let ratio = pair.ratio(appearance)
            #expect(ratio >= pair.minimum,
                    "\(pair) is \(String(format: "%.2f", ratio)):1 in \(appearance), needs \(pair.minimum):1")
        }
    }

    @Test("the pair list covers the tokens the audit flagged")
    func coversFlaggedTokens() {
        let names = Set(DS.ContrastPairs.all.map(\.foreground.name))
        for flagged in ["InkP3", "InkP4", "AccentOnFill", "StatusOk", "StatusWarn", "StatusErr"] {
            #expect(names.contains(flagged), "\(flagged) has no contrast check")
        }
        // Every paper text token is checked on every paper surface.
        #expect(DS.ContrastPairs.all.count
                >= DS.ContrastPairs.paperText.count * DS.ContrastPairs.paperSurfaces.count)
    }

    @Test("white on the accent fill fails AA, which is why labels there use onFill")
    func whiteOnAccentFails() {
        let white = DS.RGB(1, 1, 1)
        for appearance in [DS.Appearance.light, .dark] {
            let ratio = DS.RGB.contrast(white, DS.Palette.accentFill.resolved(appearance))
            #expect(ratio < 4.5)
        }
    }

    @Test("warning, accent and error stay distinguishable by colour, not only by ratio",
          arguments: [DS.Appearance.light, .dark])
    func statusHuesAreDistinct(_ appearance: DS.Appearance) {
        // All three sit near 4.5–5:1 on paper, so their lightness is alike;
        // what tells them apart is hue. ΔE (CIE76) 15+ is plainly different.
        let warn = DS.Palette.statusWarn.resolved(appearance)
        let accent = DS.Palette.accentInk.resolved(appearance)
        let err = DS.Palette.statusErr.resolved(appearance)
        #expect(Self.deltaE(warn, accent) >= 15, "warn vs accent ink in \(appearance)")
        #expect(Self.deltaE(warn, err) >= 15, "warn vs error in \(appearance)")
    }

    @Test("\"waiting on you\" stays apart from the board's other state colours",
          arguments: [DS.Appearance.light, .dark])
    func attentionIsDistinct(_ appearance: DS.Appearance) {
        let you = DS.Palette.attentionYou.resolved(appearance)
        let others: [DS.Swatch] = [DS.Palette.slotConsolidation, DS.Palette.statusOk, DS.Palette.inkP3,
                                   DS.Palette.statusErr, DS.Palette.slotDreaming]
        for other in others {
            #expect(Self.deltaE(you, other.resolved(appearance)) >= 15, "AttentionYou vs \(other.name) in \(appearance)")
        }
    }

    @Test("each KB note type's colour stands apart from the Issues colour",
          arguments: [DS.Appearance.light, .dark])
    func kbIssuesIsDistinct(_ appearance: DS.Appearance) {
        let issues = KBEntityGroup.issues.swatch.resolved(appearance)
        for group in KBEntityGroup.allCases where group != .issues {
            #expect(Self.deltaE(issues, group.swatch.resolved(appearance)) >= 15, "Issues vs \(group) in \(appearance)")
        }
        #expect(Self.deltaE(issues, DS.Palette.statusErr.resolved(appearance)) >= 15, "Issues vs the error red")
    }

    /// CIE76 colour difference between two sRGB colours (D65).
    static func deltaE(_ a: DS.RGB, _ b: DS.RGB) -> Double {
        func lab(_ c: DS.RGB) -> (Double, Double, Double) {
            func lin(_ v: Double) -> Double { v <= 0.04045 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4) }
            let r = lin(c.r), g = lin(c.g), bl = lin(c.b)
            let x = (0.4124 * r + 0.3576 * g + 0.1805 * bl) / 0.95047
            let y = 0.2126 * r + 0.7152 * g + 0.0722 * bl
            let z = (0.0193 * r + 0.1192 * g + 0.9505 * bl) / 1.08883
            func f(_ t: Double) -> Double { t > 0.008856 ? cbrt(t) : 7.787 * t + 16.0 / 116.0 }
            return (116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z)))
        }
        let (l1, a1, b1) = lab(a), (l2, a2, b2) = lab(b)
        return ((l1 - l2) * (l1 - l2) + (a1 - a2) * (a1 - a2) + (b1 - b2) * (b1 - b2)).squareRoot()
    }

    // MARK: - WCAG math

    @Test("contrast is 21:1 for black on white and 1:1 for a colour on itself")
    func contrastBounds() {
        let black = DS.RGB(0, 0, 0), white = DS.RGB(1, 1, 1)
        #expect(abs(DS.RGB.contrast(black, white) - 21) < 0.001)
        #expect(abs(DS.RGB.contrast(white, black) - 21) < 0.001)
        #expect(abs(DS.RGB.contrast(white, white) - 1) < 0.001)
    }

    @Test("relative luminance matches the WCAG reference values")
    func luminanceReference() {
        // #777777 is the classic 4.48:1-on-white grey.
        let grey = DS.RGB(0x77 / 255.0, 0x77 / 255.0, 0x77 / 255.0)
        #expect(abs(DS.RGB.contrast(grey, DS.RGB(1, 1, 1)) - 4.48) < 0.01)
    }

    @Test("a translucent colour is composited over its background")
    func compositing() {
        let halfBlack = DS.RGB(0, 0, 0, 0.5)
        let onWhite = halfBlack.over(DS.RGB(1, 1, 1))
        #expect(abs(onWhite.r - 0.5) < 0.0001 && abs(onWhite.g - 0.5) < 0.0001)
    }

    // MARK: - Tokens resolve per appearance

    @Test("an appearance plus the Increase Contrast flag picks one of the four")
    func appearanceMapping() throws {
        let aqua = try #require(NSAppearance(named: .aqua))
        let dark = try #require(NSAppearance(named: .darkAqua))
        #expect(DS.Appearance(aqua, increaseContrast: false) == .light)
        #expect(DS.Appearance(dark, increaseContrast: false) == .dark)
        #expect(DS.Appearance(aqua, increaseContrast: true) == .lightIncreasedContrast)
        #expect(DS.Appearance(dark, increaseContrast: true) == .darkIncreasedContrast)
    }

    @MainActor
    @Test("DS colours draw the palette's raw values in light and dark")
    func colorsDrawPaletteValues() throws {
        let cases: [(Color, DS.Swatch)] = [
            (DS.Ink.p3, DS.Palette.inkP3), (DS.Ink.p4, DS.Palette.inkP4),
            (DS.Paper.base, DS.Palette.paperBase), (DS.Accent.onFill, DS.Palette.accentOnFill),
        ]
        for (color, swatch) in cases {
            for name in [NSAppearance.Name.aqua, .darkAqua] {
                let ns = try #require(NSAppearance(named: name))
                var resolved: NSColor?
                ns.performAsCurrentDrawingAppearance {
                    resolved = NSColor(color).usingColorSpace(.sRGB)   // under this machine's contrast setting
                }
                let rgb = try #require(resolved)
                let expected = swatch.resolved(DS.Appearance(ns))
                #expect(abs(rgb.redComponent - expected.r) < 0.01, "\(swatch.name) \(name.rawValue)")
                #expect(abs(rgb.blueComponent - expected.b) < 0.01, "\(swatch.name) \(name.rawValue)")
            }
        }
    }

    @Test("Increase Contrast strengthens the faint inks")
    func increasedContrastIsStronger() {
        for swatch in [DS.Palette.inkP3, DS.Palette.inkP4] {
            let paper = DS.Palette.paperBase
            #expect(DS.RGB.contrast(swatch.resolved(.lightIncreasedContrast), paper.resolved(.lightIncreasedContrast))
                    > DS.RGB.contrast(swatch.resolved(.light), paper.resolved(.light)))
            #expect(DS.RGB.contrast(swatch.resolved(.darkIncreasedContrast), paper.resolved(.darkIncreasedContrast))
                    > DS.RGB.contrast(swatch.resolved(.dark), paper.resolved(.dark)))
        }
    }
}
