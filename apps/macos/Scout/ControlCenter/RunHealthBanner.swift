import SwiftUI

/// A banner across the top of `ControlCenterView` while run health is red:
/// the worst finding in plain words, how many more there are, and "Open
/// Health". Hidden otherwise. Sits under `ConnectorAlertBanner`, which
/// covers connectors; this one covers the scheduled runs themselves (UI
/// audit C8).
struct RunHealthBanner: View {
    let health: RunHealth?
    let openHealth: @MainActor () -> Void
    /// The clock the finding's age is read against; tests pin it.
    var now: Date = Date()

    var body: some View {
        if let problem = RunOutcomeDisplay.bannerProblem(health, now: now) {
            ProblemView(problem: problem,
                        actions: [ProblemAction("Open Health", perform: openHealth)],
                        style: .banner)
                .padding(.bottom, 16)
        }
    }
}
