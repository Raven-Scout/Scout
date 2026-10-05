import SwiftUI
import Testing
@testable import Scout

/// Smoke coverage for `OnboardingView`: one render per representative step,
/// each with the state that reaches its conditional branches (missing
/// prerequisites with their action buttons, a failed install step with
/// Retry, detected connectors with the Slack/GitHub follow-up fields, and the
/// Ready step's doctor summary + restart note). `ViewHost` doesn't run
/// `.task`, so `start()` and the prerequisites poll never fire here.
@MainActor
@Suite("OnboardingView — smoke", .serialized)
struct OnboardingViewSmokeTests {
    enum Scenario: String, CaseIterable { case prerequisites, failedVault, connectors, ready }

    func model(_ scenario: Scenario) -> OnboardingViewModel {
        let layout = EngineLayout(home: URL(fileURLWithPath: "/Users/alex"))
        let runner = RuleBasedRunner()
        let m = OnboardingViewModel(
            engineState: .notInstalled, layout: layout, release: nil, runner: runner,
            prerequisites: PrerequisiteChecker(runner: runner, layout: layout, resolveClaude: { _ in nil }, gitCandidates: [], uvCandidates: []),
            makeInstaller: { _ in nil }, handoff: { _ in }, appVault: URL(fileURLWithPath: "/Users/alex/Other"), onFinished: {})
        switch scenario {
        case .prerequisites:
            m.step = .prerequisites
            m.prerequisites = Prerequisites(claude: .missing, auth: .unknown, git: .missing, uv: .missing)
        case .failedVault:
            m.step = .vault
            m.progress[.bootstrapVault] = InstallProgress(step: .bootstrapVault, status: .failed("refused"), log: "install needs --user-name")
            m.lastError = "install needs --user-name"
        case .connectors:
            m.step = .connectors
            m.detections = [
                "email": ConnectorDetection(status: .connected, needsUserInput: [], evidence: ""),
                "slack": ConnectorDetection(status: .needsAuth, needsUserInput: ["user_slack_id"], evidence: ""),
            ]
            m.enabledConnectors = ["email", "slack", "github"]
            m.dailyBudget = "ten"
        case .ready:
            m.step = .ready
            m.prerequisites = Prerequisites(claude: .installed(path: URL(fileURLWithPath: "/c"), version: "2.1.259"), auth: .signedOut, git: .missing, uv: .missing)
            m.doctor = DoctorReport(severity: .red, errors: ["vault missing"], warnings: ["snapshot missing: x"])
            m.briefingStatus = "Briefing started."
        }
        return m
    }

    @Test("renders without trapping", arguments: Scenario.allCases)
    func renders(_ scenario: Scenario) {
        ViewHost.render(OnboardingView(model: model(scenario)), size: CGSize(width: 720, height: 560))
    }
}
