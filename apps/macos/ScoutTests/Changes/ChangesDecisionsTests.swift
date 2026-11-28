import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("ChangesDecisions")
struct ChangesDecisionsTests {
    private static let exe = URL(fileURLWithPath: "/usr/bin/scoutctl")
    private static let hash = String(repeating: "ab", count: 32)

    private func decisions(_ runner: RuleBasedRunner, engine: URL? = ChangesDecisionsTests.exe) -> ChangesDecisions {
        ChangesDecisions(runner: runner, scoutctl: { engine })
    }

    @Test func approvePassesHashAndSurface() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", stdout: "approved\n")
        let outcome = await decisions(runner).perform(.approve(govID: "gov-x", diffHash: Self.hash))
        #expect(outcome == .done("approved"))
        #expect(runner.calls.last?.arguments == ["changes", "approve", "gov-x", "--diff-hash", Self.hash, "--surface", "app"])
    }

    @Test func everyDecisionCarriesTheAppSurface() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl")
        let d = decisions(runner)
        _ = await d.perform(.reject(govID: "gov-x"))
        _ = await d.perform(.undo(changeID: "chg-0123456789ab"))
        _ = await d.perform(.wrong(changeID: "chg-0123456789ab"))
        _ = await d.perform(.appEdit)
        _ = await d.perform(.consent(true))
        _ = await d.perform(.diff(changeID: "chg-0123456789ab"))
        #expect(runner.calls.map(\.arguments) == [
            ["changes", "reject", "gov-x", "--surface", "app"],
            ["changes", "undo", "chg-0123456789ab", "--surface", "app"],
            ["changes", "wrong", "chg-0123456789ab", "--surface", "app"],
            ["engagement", "record", "app-edit", "--surface", "app"],
            ["engagement", "consent", "--out-of-vault", "yes"],   // consent takes no surface
            ["changes", "diff", "chg-0123456789ab"],              // a read: no surface
        ])
    }

    @Test func invalidIDsNeverSpawn() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl")
        let d = decisions(runner)
        let bad: [ChangesDecisions.Command] = [
            .undo(changeID: "chg-zz"),
            .approve(govID: "gov-X", diffHash: "abc"),
            .approve(govID: "gov-x", diffHash: "abc"),
            .approve(govID: "gov-x", diffHash: String(repeating: "AB", count: 32)),
            .approve(govID: "gov-x", diffHash: Self.hash + "\n"),
            .reject(govID: "gov-"),
            .reject(govID: "gov--x"),
            .reject(govID: "gov-x\n"),
            .reject(govID: "gov-" + String(repeating: "a", count: 62)),
            .wrong(changeID: "chg-0123456789abc"),
            .wrong(changeID: "chg-0123456789ab\n"),
            .diff(changeID: "chg-0123456789AB"),
            .diff(changeID: "--help"),
        ]
        for command in bad { #expect(await d.perform(command) == .invalid, "\(command)") }
        #expect(runner.calls.isEmpty)
    }

    @Test func theBoundariesOfTheIDRulesAreAccepted() {
        #expect(ChangesDecisions.isGovID("gov-" + String(repeating: "a", count: 61)))   // {0,60} after the first
        #expect(ChangesDecisions.isGovID("gov-a"))
        #expect(ChangesDecisions.isGovID("gov-0-a-b"))
        #expect(ChangesDecisions.isChangeID("chg-000000000000"))
        #expect(ChangesDecisions.isDiffHash(Self.hash))
    }

    @Test func aLiveRunRefusalIsWordedForTheOwner() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", stderr: "scoutctl changes undo: the owner's decision; a scheduled run may not make it\n", exit: 2)
        let outcome = await decisions(runner).perform(.undo(changeID: "chg-0123456789ab"))
        #expect(outcome == .refused("Scout is running right now. Try again when the run finishes."))
    }

    @Test func anyOtherRefusalIsTheEnginesFirstLine() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", stderr: "\nscoutctl changes wrong: no such change\nsecond line\n", exit: 2)
        let outcome = await decisions(runner).perform(.wrong(changeID: "chg-0123456789ab"))
        #expect(outcome == .refused("no such change"))   // m-d: the command prefix never reaches the owner
    }

    @Test func onlyALeadingScoutctlCommandPrefixIsTakenOff() {
        #expect(ChangesDecisions.withoutCommandPrefix("scoutctl changes undo: Scout can't undo this change.")
                == "Scout can't undo this change.")
        #expect(ChangesDecisions.withoutCommandPrefix("scoutctl auto-rollback: off only") == "off only")
        for line in ["Scout can't: keep me", "scoutctl changes Undo: Mixed case", "scoutctl changes undo:",
                     "scoutctl changes undo: ", "scoutctl's note: keep", "no colon here"] {
            #expect(ChangesDecisions.withoutCommandPrefix(line) == line, "\(line)")
        }
    }

    @Test func aSilentRefusalStillSaysSomething() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", exit: 1)
        #expect(await decisions(runner).perform(.appEdit) == .refused("The engine said no (exit 1)."))
    }

    @Test func noEngineAndNoSpawnAreRefusals() async {
        let none = RuleBasedRunner()
        #expect(await decisions(none, engine: nil).perform(.appEdit) == .refused("Scout's engine isn't set up yet."))
        #expect(none.calls.isEmpty)
        let broken = RuleBasedRunner()   // no rules: the spawn throws
        #expect(await decisions(broken).perform(.appEdit) == .refused("Couldn't run Scout's engine."))
    }

    @Test func diffStdoutIsThePayloadAsPrinted() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", stdout: "  - old\n  + new\n\n")
        let outcome = await decisions(runner).perform(.diff(changeID: "chg-0123456789ab"))
        #expect(outcome == .done("  - old\n  + new\n\n"))
    }

    /// `engagement consent --show` prints exactly `yes`, `no` or `not asked` (engine `cli_engagement_consent`).
    /// Anything else, and a failed read, is `unknown`: the tab asks nothing until it knows.
    @Test func consentShowMapsTheEnginesThreeAnswers() async {
        for (printed, expected) in [("yes\n", ConsentAnswer.yes), ("no\n", .no), ("not asked\n", .notAsked),
                                    ("maybe\n", .unknown), ("", .unknown)] {
            let runner = RuleBasedRunner()
            runner.on(tool: "scoutctl", stdout: printed)
            let d = decisions(runner)
            #expect(d.consent == .unknown)
            _ = await d.perform(.consentShow)
            #expect(d.consent == expected, "\(printed)")
            #expect(runner.calls.last?.arguments == ["engagement", "consent", "--show"])
        }
    }

    @Test func settingConsentUpdatesTheAnswerOnlyWhenTheEngineAccepts() async {
        let ok = RuleBasedRunner()
        ok.on(tool: "scoutctl")
        let d = decisions(ok)
        _ = await d.perform(.consent(false))
        #expect(d.consent == .no)
        #expect(ok.calls.last?.arguments == ["engagement", "consent", "--out-of-vault", "no"])

        let refused = RuleBasedRunner()
        refused.on(tool: "scoutctl", stderr: "a scheduled run may not write it", exit: 2)
        let d2 = decisions(refused)
        _ = await d2.perform(.consent(true))
        #expect(d2.consent == .unknown)
    }

    /// The tab adds no write path beyond the plan's allow-list. Every word-like string literal in
    /// `ChangesDecisions.swift` (a subcommand, a value or a flag) must be on it: a new command fails here.
    @Test func onlyAllowedCommandsAppearInSource() throws {
        let file = URL(fileURLWithPath: #filePath)   // …/apps/macos/ScoutTests/Changes/ChangesDecisionsTests.swift
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout/Changes/ChangesDecisions.swift")
        let source = try String(contentsOf: file, encoding: .utf8)
        let code = source.split(separator: "\n", omittingEmptySubsequences: false)
            .filter { !$0.trimmingCharacters(in: .whitespaces).hasPrefix("//") }.joined(separator: "\n")
        let literals = try NSRegularExpression(pattern: #""([^"\\ ]*)""#).matches(in: code, range: NSRange(code.startIndex..., in: code))
            .compactMap { Range($0.range(at: 1), in: code).map { String(code[$0]) } }
        let allowed: Set<String> = [
            // subcommands and the words after them
            "changes", "approve", "reject", "undo", "wrong", "diff", "engagement", "consent", "record", "app-edit",
            // flags and their fixed values
            "--diff-hash", "--surface", "app", "--out-of-vault", "--show", "yes", "no",
            // the id prefixes the validators check, and the consent answers `--show` prints
            "chg-", "gov-", "not",
        ]
        let words = literals.filter { $0.count > 1 }   // single characters are byte ranges in the id validators
        #expect(words.contains("approve") && words.contains("app-edit"))
        for word in words { #expect(allowed.contains(word), "unexpected literal \(word) in ChangesDecisions.swift") }
        for forbidden in ["apply", "discard", "rollback", "auto-rollback", "scan", "announced", "calibrated", "due"] {
            #expect(!words.contains(forbidden))
        }
        for word in ["FileManager", "contentsOf", "write(to"] { #expect(!source.contains(word), "mentions \(word)") }
    }
}
