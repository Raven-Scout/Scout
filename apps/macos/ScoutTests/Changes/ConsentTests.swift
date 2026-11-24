import Foundation
import SwiftUI
import Testing
@testable import Scout

@MainActor
@Suite("Consent question", .serialized)
struct ConsentTests {
    private static let exe = URL(fileURLWithPath: "/usr/bin/scoutctl")

    private func decisions(show: String? = nil, showExit: Int32 = 0) -> (RuleBasedRunner, ChangesDecisions) {
        let runner = RuleBasedRunner()
        if let show { runner.on(tool: "scoutctl", prefix: ["engagement", "consent", "--show"], stdout: show, exit: showExit) }
        runner.on(tool: "scoutctl", prefix: ["engagement", "consent"])
        return (runner, ChangesDecisions(runner: runner, scoutctl: { Self.exe }))
    }

    private func text(_ view: some View) async -> String {
        let nodes = await AccessibilityTree.nodes(of: view.frame(width: 700), size: CGSize(width: 700, height: 500))
        return nodes.flatMap { [$0.label, $0.value] }.filter { !$0.isEmpty }.joined(separator: "\n")
    }

    // MARK: - Loading the answer

    @Test func consentIsUnknownUntilTheEngineIsAsked() async {
        let (_, d) = decisions(show: "not asked\n")
        #expect(d.consent == .unknown)
        #expect(!ChangesView.showsConsentCard(d.consent))
        _ = await d.perform(.consentShow)
        #expect(d.consent == .notAsked)
        #expect(ChangesView.showsConsentCard(d.consent))
    }

    @Test func cardShowsOnlyAfterAShowThatPrintsNotAsked() async {
        for (out, expected) in [("yes\n", ConsentAnswer.yes), ("no\n", .no), ("garbage\n", .unknown)] {
            let (_, d) = decisions(show: out)
            _ = await d.perform(.consentShow)
            #expect(d.consent == expected)
            #expect(!ChangesView.showsConsentCard(d.consent), "\(out)")
        }
        // Engine missing: nothing to ask, nothing to show.
        let missing = ChangesDecisions(runner: RuleBasedRunner(), scoutctl: { nil })
        _ = await missing.perform(.consentShow)
        #expect(!ChangesView.showsConsentCard(missing.consent))
    }

    @Test func consentCardOnlyWhenNotAsked() async throws {
        let service = try await LoadedChangesService.make()
        for (out, shown) in [("not asked\n", true), ("yes\n", false), ("no\n", false)] {
            let (_, d) = decisions(show: out)
            _ = await d.perform(.consentShow)
            let view = ChangesView().environmentObject(service).environmentObject(d)
            let t = await text(view)
            #expect(t.contains(ConsentCard.title) == shown, "\(out)")
        }
        // Not loaded at all: no card.
        let unloaded = ChangesView().environmentObject(service).environmentObject(LoadedChangesService.decisions())
        #expect(!(await text(unloaded)).contains(ConsentCard.title))
    }

    // MARK: - The card

    @Test func theCardSaysWhatItAsks() async {
        let (_, d) = decisions()
        let t = await text(ConsentCard(decisions: d))
        #expect(t.contains("Count your Claude sessions outside the vault?"))
        #expect(t.contains("Scout learns what's useful by noticing when you rely on it. With your OK it also notes sessions outside the vault that used Scout, only that one did, never what it read or where. You can change this in Settings."))
        #expect(t.contains("Yes, count them"))
        #expect(t.contains("No"))
    }

    @Test func consentYesSendsTheAnswer() async {
        let (runner, d) = decisions()
        await ConsentCard.answer(true, decisions: d)
        #expect(runner.calls.last?.arguments == ["engagement", "consent", "--out-of-vault", "yes"])
        #expect(d.consent == .yes)
    }

    @Test func consentNoSendsTheAnswer() async {
        let (runner, d) = decisions()
        await ConsentCard.answer(false, decisions: d)
        #expect(runner.calls.last?.arguments == ["engagement", "consent", "--out-of-vault", "no"])
        #expect(d.consent == .no)
    }

    @Test func aRefusedAnswerSaysWhyAndChangesNothing() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", stderr: "scoutctl: a scheduled run may not do that\n", exit: 2)
        let d = ChangesDecisions(runner: runner, scoutctl: { Self.exe })
        let message = await ConsentCard.answer(true, decisions: d)
        #expect(message == ChangesDecisions.liveRunMessage)
        #expect(d.consent == .unknown)
    }

    // MARK: - Settings

    @Test func settingsNamesTheAnswerInPlainWords() {
        let words = [ConsentAnswer.yes, .no, .notAsked, .unknown].map(ChangesMapSettingsSection.answerWords)
        #expect(Set(words).count == 4)
        #expect(words.allSatisfy { !$0.isEmpty })
        #expect(ChangesMapSettingsSection.answerWords(.yes).contains("Counting"))
        #expect(ChangesMapSettingsSection.answerWords(.no).contains("Not counting"))
    }

    @Test func settingsLetsTheOwnerChangeTheAnswer() async {
        let (runner, d) = decisions(show: "yes\n")
        _ = await d.perform(.consentShow)
        let t = await text(SettingsCard { ChangesMapSettingsSection(decisions: d) }.frame(width: 640))
        #expect(t.contains("Counting"), "\(t)")
        #expect(ChangesMapSettingsSection.change(from: .yes) == false)
        #expect(ChangesMapSettingsSection.change(from: .no) == true)
        #expect(ChangesMapSettingsSection.change(from: .notAsked) == true)
        #expect(ChangesMapSettingsSection.change(from: .unknown) == nil)
        await ChangesMapSettingsSection.apply(false, decisions: d)
        #expect(runner.calls.last?.arguments == ["engagement", "consent", "--out-of-vault", "no"])
        #expect(d.consent == .no)
    }
}
