import SwiftUI

/// A change's verdict as a pill. The colours are the design system's own tones, picked by the same
/// `TrendModel.tone` the trend's markers use, so a card and its marker always agree: `helped` green,
/// testing amber, `hurt` and `rolled back` red, everything else grey. A hollow ring before the words
/// means the reason for the change was guessed from the commit.
struct VerdictPill: View {
    let test: ChangeTest
    /// `why.inferred`: the reason was guessed, so the marker is hollow.
    let inferred: Bool

    /// The pill's words: the verdict itself, else where the test stands.
    static func words(_ test: ChangeTest) -> String {
        if let verdict = test.verdict, !verdict.isEmpty { return verdict }
        return test.state == "testing" ? "testing" : "not tested"
    }

    static func tone(_ test: ChangeTest) -> DS.PillTone {
        switch TrendModel.tone(test) {
        case .helped: return .ok
        case .testing: return .warn
        case .bad: return .err
        case .neutral: return .neutral
        }
    }

    /// What a screen reader says: the words, and that the reason was guessed when it was.
    static func spokenLabel(_ test: ChangeTest, inferred: Bool) -> String {
        "Verdict: \(words(test))" + (inferred ? ", reason guessed from the commit" : "")
    }

    var body: some View {
        DSPill(Self.words(test), tone: Self.tone(test), symbol: inferred ? "circle" : nil)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(Self.spokenLabel(test, inferred: inferred))
    }
}
