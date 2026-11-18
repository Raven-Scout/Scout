import Foundation
import Testing
@testable import Scout

/// R6: what onboarding tells a new owner — step names and count, a repair
/// that says it's a repair, the Ready step's health check in plain words, and
/// the pickers behind the free-text fields (audit #88–#90).
@MainActor
@Suite("Onboarding — plain words")
struct OnboardingPlainWordsTests {

    func model(_ engineState: EngineState = .notInstalled) -> OnboardingViewModel {
        let layout = EngineLayout(home: URL(fileURLWithPath: "/Users/alex"))
        let runner = RuleBasedRunner()
        return OnboardingViewModel(
            engineState: engineState, layout: layout, release: nil, runner: runner,
            prerequisites: PrerequisiteChecker(runner: runner, layout: layout, resolveClaude: { _ in nil }, gitCandidates: [], uvCandidates: []),
            makeInstaller: { _ in nil }, handoff: { _ in }, onFinished: {})
    }

    @Test("every step has a name, and they count 1 to 7")
    func stepsAreNamedAndCounted() {
        let steps = OnboardingViewModel.Step.allCases
        #expect(OnboardingViewModel.Step.count == 7)
        #expect(steps.map(\.number) == Array(1...7))
        #expect(Set(steps.map(\.title)).count == steps.count)
        #expect(steps.allSatisfy { !$0.title.isEmpty })
    }

    @Test("a broken engine is a repair, not a first-time setup")
    func brokenEngineSaysRepair() {
        #expect(model().flowTitle == "Set up Scout")
        #expect(!model().isRepair)
        let broken = model(.broken(nil, reason: "engine pointer names a missing scoutctl: /s"))
        #expect(broken.isRepair)
        #expect(broken.flowTitle == "Repair Scout")
    }

    @Test("a chosen folder inside home is written with ~")
    func chosenFolderUsesTilde() {
        let home = URL(fileURLWithPath: "/Users/alex")
        #expect(OnboardingViewModel.displayPath(URL(fileURLWithPath: "/Users/alex/Scout"), home: home) == "~/Scout")
        #expect(OnboardingViewModel.displayPath(URL(fileURLWithPath: "/Users/alex"), home: home) == "~")
        #expect(OnboardingViewModel.displayPath(URL(fileURLWithPath: "/Users/alexander/Scout"), home: home) == "/Users/alexander/Scout")
        #expect(OnboardingViewModel.displayPath(URL(fileURLWithPath: "/Volumes/Notes/Scout"), home: home) == "/Volumes/Notes/Scout")

        let m = model()
        m.chooseVault(URL(fileURLWithPath: "/Users/alex/Documents/Scout"))
        #expect(m.vaultPath == "~/Documents/Scout")
        #expect(m.vaultPathIsValid)
        #expect(m.vaultURL.path == "/Users/alex/Documents/Scout")
    }

    /// #688 review: Return in a text field must not install the engine or
    /// create the Scout folder; those Continues say what they do.
    @Test("Continue that changes the Mac names it and takes a click")
    func machineChangingContinueTakesAClick() {
        #expect(OnboardingViewModel.continueTitle(for: .prerequisites) == "Install engine")
        #expect(OnboardingViewModel.continueTitle(for: .connectors) == "Create Scout folder")
        let m = model()
        for step in [OnboardingViewModel.Step.prerequisites, .connectors] {
            m.step = step
            #expect(!m.continueTakesReturn, "\(step)")
        }
        // Engine and Scout folder with nothing installed yet offer Install,
        // which runs the install too, so Return goes to neither button.
        for step in [OnboardingViewModel.Step.engine, .vault] {
            m.step = step
            #expect(m.canRetry)
            #expect(!m.continueTakesReturn, "\(step)")
        }
        m.step = .identity
        #expect(m.continueTakesReturn)
        #expect(m.continueTitle == "Continue")
    }

    @Test("the time-zone picker keeps a value the system doesn't list")
    func timezoneChoicesKeepTheCurrentValue() {
        let choices = OnboardingViewModel.timezoneChoices(including: "Europe/Prague")
        #expect(choices.contains("Europe/Prague"))
        #expect(choices == choices.sorted())
        #expect(OnboardingViewModel.timezoneChoices(including: "Mars/Olympus").contains("Mars/Olympus"))
        #expect(!OnboardingViewModel.timezoneChoices(including: "").contains(""))
    }

    @Test("the Ready step never prints green, yellow or red")
    func readySummaryIsPlain() {
        let green = OnboardingViewModel.readySummary(DoctorReport(severity: .green, errors: [], warnings: []))
        #expect(green.tone == .ok)
        let yellow = OnboardingViewModel.readySummary(DoctorReport(severity: .yellow, errors: [], warnings: ["a", "b"]))
        #expect(yellow.tone == .warn)
        #expect(yellow.title.contains("2 things"))
        let one = OnboardingViewModel.readySummary(DoctorReport(severity: .yellow, errors: [], warnings: ["a"]))
        #expect(one.title.contains("1 thing "))
        let none = OnboardingViewModel.readySummary(DoctorReport(severity: .yellow, errors: [], warnings: []))
        #expect(none.tone == .warn)
        #expect(!none.title.contains("0"), "\(none.title)")
        let red = OnboardingViewModel.readySummary(DoctorReport(severity: .red, errors: ["vault missing"], warnings: ["w"]))
        #expect(red.tone == .err)
        #expect(red.details == ["vault missing", "w"])
        for s in [green, yellow, one, none, red] {
            let text = (s.title + " " + s.detail).lowercased()
            for word in ["green", "yellow", "red", "doctor"] {
                #expect(!text.contains(word), "\"\(word)\" in \(s.title) / \(s.detail)")
            }
        }
    }
}
