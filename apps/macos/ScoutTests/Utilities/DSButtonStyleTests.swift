import Testing
@testable import Scout

/// The design-system v2 button colours (#105): every label is readable on
/// what it sits on, and the states read one way across roles.
@Suite("Design-system buttons")
struct DSButtonStyleTests {

    @Test("every chromed button label is a checked contrast pair, enabled and disabled")
    func chromedLabelsAreChecked() {
        let checked = Set(DS.ContrastPairs.all.map { "\($0.foreground.name) on \($0.background.name)" })
        for role in DS.ButtonRole.allCases {
            for enabled in [true, false] {
                let c = DS.buttonColors(role, enabled: enabled)
                guard let fill = c.fill else { continue }
                #expect(checked.contains("\(c.label.name) on \(fill.name)"),
                        "\(role) (enabled: \(enabled)) draws \(c.label.name) on \(fill.name) with no contrast check")
            }
        }
    }

    @Test("a link's label is paper text, so it is checked on every paper surface")
    func linkLabelsArePaperText() {
        let paperText = Set(DS.ContrastPairs.paperText.map(\.name))
        for enabled in [true, false] {
            let c = DS.buttonColors(.link, enabled: enabled)
            #expect(c.fill == nil)
            #expect(paperText.contains(c.label.name), "link label \(c.label.name) isn't checked on paper")
        }
        // Destructive's red label also sits on paper-coloured chrome.
        #expect(paperText.contains(DS.buttonColors(.destructive, enabled: true).label.name))
    }

    @Test("a quiet action is grey paper text, darker on hover, never accent")
    func quietIsGreyPaperText() {
        let paperText = Set(DS.ContrastPairs.paperText.map(\.name))
        let quiet = DS.buttonColors(.quiet, enabled: true)
        #expect(quiet.fill == nil && quiet.stroke == nil)
        #expect(paperText.contains(quiet.label.name))
        #expect(paperText.contains(DS.quietHoverLabel.name))
        #expect(DS.quietHoverLabel.name != quiet.label.name)
        #expect(quiet.label.name != DS.Palette.accentInk.name, "a quiet action must not read as a link")
        #expect(DS.buttonColors(.quiet, enabled: false).label.name == DS.Palette.inkP4.name)
    }

    @Test("disabled reads one way for every role, and unlike any enabled role")
    func disabledIsOneLook() {
        // One label colour for every role; one well behind every role that has chrome.
        let disabled = DS.ButtonRole.allCases.map { DS.buttonColors($0, enabled: false) }
        #expect(Set(disabled.map(\.label.name)).count == 1)
        #expect(Set(disabled.compactMap { $0.fill?.name }).count == 1)
        for role in DS.ButtonRole.allCases {
            #expect(DS.buttonColors(role, enabled: true).label.name != disabled[0].label.name,
                    "\(role) enabled uses the disabled label colour")
        }
    }

    @Test("the five roles are visibly different")
    func rolesDiffer() {
        let looks = DS.ButtonRole.allCases.map { role -> String in
            let c = DS.buttonColors(role, enabled: true)
            return "\(c.label.name)/\(c.fill?.name ?? "-")"
        }
        #expect(Set(looks).count == DS.ButtonRole.allCases.count)
        #expect(DS.buttonColors(.primary, enabled: true).fill?.name == DS.Palette.accentFill.name)
    }

    @Test("sizes use the semantic type scale")
    func sizesUseTheScale() {
        #expect(DS.ButtonSize.regular.textStyle == .label)
        #expect(DS.ButtonSize.small.textStyle == .caption)
        #expect(DS.ButtonSize.regular.height > DS.ButtonSize.small.height)
    }
}
