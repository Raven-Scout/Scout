import SwiftUI

/// Run-health warnings about one schedule ("Hasn't run for 3 days"), drawn
/// under its row or card on the Schedules page. Read from the run ledger's
/// `slot.*` findings (`RunOutcomeDisplay.slotWarnings`); the Health page has
/// the fix. Draws nothing when the schedule is fine.
struct SlotWarningLines: View {
    let warnings: [RunOutcomeDisplay.SlotWarning]

    var body: some View {
        if !warnings.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(warnings) { warning in
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        Image(systemName: ProblemView.symbol(warning.severity))
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(ProblemView.tint(warning.severity))
                            .accessibilityHidden(true)
                        Text(warning.text)
                            .font(DS.sans(11))
                            .foregroundStyle(DS.Ink.p2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("\(ProblemView.severityWord(warning.severity)): \(warning.text)")
                    .help("Details on the Health page")
                }
            }
        }
    }
}
