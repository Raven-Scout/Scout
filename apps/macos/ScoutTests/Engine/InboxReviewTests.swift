import Foundation
import Testing
@testable import Scout

/// Settings ▸ Agent copies ▸ Review (#550 V4b3): the `scoutctl inbox review … --json` contract, the words the
/// confirmations use, and the only commands the sheet runs.
///
/// `inbox-review.json` is a byte copy of the engine's generated
/// `plugin/engine/tests/fixtures/contract/inbox-review.json` (root CLAUDE.md): one document with each command's
/// output under its own key. When it is regenerated, update the pins in `decodesTheSharedFixture`.
@Suite("Inbox review")
@MainActor
struct InboxReviewTests {
    private final class FixtureAnchor {}

    static func fixture() throws -> [String: Any] {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: "inbox-review", withExtension: "json"))
        return try #require(try JSONSerialization.jsonObject(with: try Data(contentsOf: url)) as? [String: Any])
    }

    /// One command's output, as scoutctl prints it.
    static func part(_ key: String) throws -> Data {
        let value = try #require(try fixture()[key])
        return try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    }

    static func text(_ key: String) throws -> String { String(decoding: try part(key), as: UTF8.self) }

    static let observation = "01M7SQZHT000000000000000AB"
    static let promoted = "01M7ST8SJ000000000000000JK"
    static let kept = "01M7SSPFM000000000000000GH"

    // MARK: - The contract

    @Test func decodesTheSharedFixture() throws {
        let items = try #require(InboxReview.items(try Self.part("review")))
        #expect(items.map(\.ulid) == [Self.observation, "01M7SRHVR000000000000000CD", "01M7SS45P000000000000000EF"])
        let obs = items[0], comment = items[1], limited = items[2]
        #expect(obs.kind == "observation" && obs.status == "waiting" && obs.delegate == "grok-personal")
        #expect(obs.text.contains(#"next\u{00A0}p"#))   // the engine shows the no-break space
        #expect(obs.lookAlikes == ["p\u{0430}yment"])
        #expect(obs.about == "Parts: supplier" && obs.source == "https://example.com/supplier-update")
        #expect(obs.canAccept && obs.sha256 == nil && obs.audience == nil)
        #expect(comment.kind == "comment" && comment.status == "held" && comment.tag == "AB12" && comment.canAccept)
        #expect(limited.kind == "op" && limited.status == "limited" && limited.op == "mark-done" && !limited.canAccept)

        let accept = try #require(InboxReview.decision(try Self.part("accept")))
        #expect(accept.outcome == "accepted" && accept.ulid == Self.kept)
        #expect(accept.notes.contains { $0.contains("can't be promoted or reverted") })

        let accepted = try #require(InboxReview.accepted(try Self.part("accepted")))
        #expect(accepted.map(\.ulid) == [Self.kept, Self.promoted])
        #expect(!accepted[0].promoted && accepted[0].promotedTo == nil)
        #expect(accepted[1].promoted && accepted[1].promotedTo == ["grok-personal", "duet", "research-bot"])

        let preview = try #require(InboxReview.preview(try Self.part("promote_preview")))
        #expect(preview.ulid == Self.promoted && preview.status == "accepted")
        #expect(preview.audience == ["grok-personal", "duet", "research-bot"])
        #expect(preview.sha256 == "5b1dac7154ace241fba063394ab879dce343a4c69498ed74a7fa93669c5020dd")
        #expect(preview.text.contains("> The team meeting moved to Thursdays."))
        #expect(preview.about == "Observation from grok-personal" && preview.source == nil)   // the title it publishes

        #expect(try #require(InboxReview.decision(try Self.part("promote"))).outcome == "promoted")
        let back = try #require(InboxReview.preview(try Self.part("unpromote_preview")))
        #expect(back.status == "promoted" && back.audience == preview.audience)

        let refusal = try #require(InboxReview.refusal(try Self.part("refusal")))
        #expect(refusal.code == "already_promoted" && refusal.message == "refused: \(Self.promoted) is promoted already")
        #expect(InboxReview.decision(try Self.part("refusal")) == nil)
    }

    @Test func aPreviewWithoutAHashOrAudienceIsNotOne() throws {
        #expect(InboxReview.preview(try JSONSerialization.data(withJSONObject:
            try #require(try Self.fixture()["review"] as? [[String: Any]])[0])) == nil)
    }

    @Test func idsAndHashesAreCheckedBeforeTheyReachScoutctl() {
        #expect(InboxReview.isULID(Self.observation))
        for bad in ["", "01M7SQZHT000000000000000A", "01M7SQZHT000000000000000ABI", "81M7SQZHT000000000000000AB",
                    "01m7sqzht000000000000000ab", Self.observation + "\n", "--confirm"] {
            #expect(!InboxReview.isULID(bad), "\(bad)")
        }
        #expect(InboxReview.isSHA256(String(repeating: "a", count: 64)))
        #expect(!InboxReview.isSHA256(String(repeating: "A", count: 64)) && !InboxReview.isSHA256("abc"))
    }

    // MARK: - The words

    @Test func theConfirmationsNameWhoItConcerns() throws {
        let obs = try #require(InboxReview.items(try Self.part("review")))[0]
        #expect(InboxReview.header(obs) == "FROM AGENT delegate-grok-personal · observation · waiting")
        #expect(InboxReview.acceptButton(obs) == "Accept from delegate-grok-personal")
        #expect(InboxConfirmView.title(.accept(obs)) == "Accept this text from delegate-grok-personal?")
        #expect(InboxConfirmView.detail(.accept(obs)).contains("private"))
        let preview = try #require(InboxReview.preview(try Self.part("promote_preview")))
        #expect(InboxReview.promoteButton(preview) == "Promote to delegate-grok-personal, delegate-duet, delegate-research-bot")
        #expect(InboxConfirmView.detail(.promote(preview)).contains("An agent added later won't"))
        let back = try #require(InboxReview.preview(try Self.part("unpromote_preview")))
        #expect(InboxReview.unpromoteButton(back)
                == "Make private again: hide from delegate-grok-personal, delegate-duet, delegate-research-bot")
        let comment = try #require(InboxReview.items(try Self.part("review")))[1]
        #expect(InboxReview.header(comment) == "FROM AGENT delegate-grok-personal · comment add-comment [#AB12] 2026-11-19 · held")
        #expect(InboxConfirmView.detail(.accept(comment)).contains("never as yours"))
    }

    @Test func aPromoteNoOneWouldSeeOffersNoButton() throws {
        var obj = try #require(try Self.fixture()["promote_preview"] as? [String: Any])
        obj["audience"] = [String]()
        let preview = try #require(InboxReview.preview(try JSONSerialization.data(withJSONObject: obj)))
        #expect(InboxReview.promoteButton(preview) == nil && InboxConfirmView.button(.promote(preview)) == nil)
        #expect(InboxConfirmView.detail(.promote(preview)).contains("no one to promote it to"))
    }

    @Test func acceptedNotesReadInPlainWords() throws {
        let accepted = try #require(InboxReview.accepted(try Self.part("accepted")))
        #expect(InboxReviewSheet.acceptedState(accepted[0]) == "Accepted on 2026-11-19; private, seen by no agent.")
        #expect(InboxReviewSheet.acceptedState(accepted[1])
                == "Accepted on 2026-11-19; promoted to delegate-grok-personal, delegate-duet, delegate-research-bot.")
    }

    // MARK: - The service

    nonisolated private static let exe = URL(fileURLWithPath: "/engine/bin/scoutctl")

    private func runner() throws -> RuleBasedRunner {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["inbox", "review", "--accepted"], stdout: try Self.text("accepted"))
        runner.on(tool: "scoutctl", prefix: ["inbox", "review", "--delegate"], stdout: try Self.text("review"))
        runner.on(tool: "scoutctl", prefix: ["inbox", "review", "accept"], stdout: try Self.text("accept"))
        runner.on(tool: "scoutctl", prefix: ["inbox", "review", "reject"],
                  stdout: #"{"ok": true, "delegate": "grok-personal", "ulid": "01M7SQZHT000000000000000AB", "outcome": "rejected", "notes": []}"#)
        runner.on({ _, args in args.starts(with: ["inbox", "review", "promote"]) && args.contains("--preview") }) { _, _, _ in
            ProcessResult(exitCode: 0, stdout: try Self.part("promote_preview"), stderr: Data())
        }
        runner.on({ _, args in args.starts(with: ["inbox", "review", "unpromote"]) && args.contains("--preview") }) { _, _, _ in
            ProcessResult(exitCode: 0, stdout: try Self.part("unpromote_preview"), stderr: Data())
        }
        runner.on(tool: "scoutctl", prefix: ["inbox", "review", "promote"], stdout: try Self.text("promote"))
        runner.on(tool: "scoutctl", prefix: ["inbox", "review", "unpromote"],
                  stdout: #"{"ok": true, "delegate": "grok-personal", "ulid": "01M7ST8SJ000000000000000JK", "outcome": "unpromoted", "notes": []}"#)
        return runner
    }

    private func service(_ runner: RuleBasedRunner, exe: URL? = InboxReviewTests.exe) -> InboxReviewService {
        InboxReviewService(runner: runner, environment: ["SCOUT_DATA_DIR": "/v"], scoutctl: { exe })
    }

    private static let reads = [["inbox", "review", "--delegate", "grok-personal", "--json"],
                                ["inbox", "review", "--accepted", "--delegate", "grok-personal", "--json"]]

    @Test func openingOnlyReads() async throws {
        let runner = try runner()
        let svc = service(runner)
        await svc.open("grok-personal")
        #expect(runner.calls(to: "scoutctl") == Self.reads)
        #expect(runner.calls.first?.environment["SCOUT_DATA_DIR"] == "/v")
        #expect(svc.items.count == 3 && svc.accepted.count == 2 && svc.message == nil && svc.work == nil)
    }

    @Test func acceptPassesTheULIDTwiceAndSaysItStaysPrivate() async throws {
        let runner = try runner()
        let svc = service(runner)
        await svc.open("grok-personal")
        await svc.accept(svc.items[0])
        #expect(runner.calls(to: "scoutctl").dropFirst(2).first
                == ["inbox", "review", "accept", Self.observation, "--confirm", Self.observation, "--json"])
        #expect(svc.message?.contains(InboxReview.acceptedObservationNote) == true && !svc.failed)
    }

    @Test func anOpOverTheLimitIsNeverAccepted() async throws {
        let runner = try runner()
        let svc = service(runner)
        await svc.open("grok-personal")
        await svc.accept(svc.items[2])
        #expect(runner.calls(to: "scoutctl") == Self.reads)
        await svc.reject(svc.items[2])
        #expect(runner.calls(to: "scoutctl").dropFirst(2).first
                == ["inbox", "review", "reject", "01M7SS45P000000000000000EF", "--json"])
    }

    @Test func promoteSendsBackThePreviewsHashAndNamesExactlyItsAudience() async throws {
        let runner = try runner()
        let svc = service(runner)
        await svc.open("grok-personal")
        let preview = try #require(await svc.previewPromote(svc.accepted[1]))
        #expect(runner.calls(to: "scoutctl").last == ["inbox", "review", "promote", Self.promoted, "--preview", "--json"])
        #expect(runner.calls(to: "scoutctl").count == 3)   // a preview doesn't reload
        await svc.promote(preview)
        #expect(runner.calls(to: "scoutctl")[3] == [
            "inbox", "review", "promote", Self.promoted, "--to", "grok-personal,duet,research-bot",
            "--previewed", "5b1dac7154ace241fba063394ab879dce343a4c69498ed74a7fa93669c5020dd",
            "--confirm", Self.promoted, "--json",
        ])
        #expect(svc.message?.contains("Visible to grok-personal, duet, research-bot") == true)
    }

    @Test func unpromoteSendsBackThePreviewsHash() async throws {
        let runner = try runner()
        let svc = service(runner)
        await svc.open("grok-personal")
        let back = try #require(await svc.previewUnpromote(svc.accepted[1]))
        await svc.unpromote(back)
        #expect(runner.calls(to: "scoutctl")[3] == ["inbox", "review", "unpromote", Self.promoted, "--previewed",
                                                    "004dcda7593a3c700f9b1db08f64a86f0eafd085d114469e775ae2bb09ac81a7",
                                                    "--json"])
        #expect(svc.message?.hasPrefix("Private again") == true)
    }

    @Test func aRefusalIsShownInTheEnginesWords() async throws {
        let runner = RuleBasedRunner()
        let refusal = try Self.text("refusal")
        runner.on({ _, args in args.contains("--preview") }) { _, _, _ in
            ProcessResult(exitCode: 2, stdout: Data(refusal.utf8), stderr: Data())
        }
        runner.on(tool: "scoutctl", prefix: ["inbox", "review", "--accepted"], stdout: try Self.text("accepted"))
        runner.on(tool: "scoutctl", prefix: ["inbox", "review", "--delegate"], stdout: try Self.text("review"))
        let svc = service(runner)
        await svc.open("grok-personal")
        #expect(await svc.previewPromote(svc.accepted[0]) == nil)
        #expect(svc.failed && svc.message == "refused: \(Self.promoted) is promoted already")
    }

    @Test func nothingRunsForAnItemOrIdTheEngineDidNotList() async throws {
        let runner = try runner()
        let svc = service(runner)
        await svc.open("Not An Id")
        #expect(runner.calls.isEmpty)
        await svc.open("grok-personal")
        var forged = try #require(try Self.fixture()["review"] as? [[String: Any]])[0]
        forged["ulid"] = "01M7SQZHT000000000000000ZZ"
        let item = try JSONDecoder().decode(InboxReview.Item.self, from: JSONSerialization.data(withJSONObject: forged))
        await svc.accept(item)
        await svc.reject(item)
        #expect(runner.calls(to: "scoutctl") == Self.reads)
    }

    @Test func aPreviewThatNamesNoOneIsRefusedWithAMessage() async throws {
        let runner = try runner()
        let svc = service(runner)
        await svc.open("grok-personal")
        var obj = try #require(try Self.fixture()["promote_preview"] as? [String: Any])
        obj["audience"] = ["author"]   // reserved: never a delegate id
        let preview = try JSONDecoder().decode(InboxReview.Item.self, from: JSONSerialization.data(withJSONObject: obj))
        await svc.promote(preview)
        #expect(runner.calls(to: "scoutctl") == Self.reads)
        #expect(svc.failed && svc.message?.hasPrefix("Nothing was promoted") == true)
        #expect(!DelegateStatus.isDelegateID("author") && DelegateStatus.isDelegateID("co-author"))
    }

    @Test func noEngineRunsNothing() async {
        let runner = RuleBasedRunner()
        let svc = service(runner, exe: nil)
        await svc.open("grok-personal")
        #expect(runner.calls.isEmpty && svc.failed && svc.message == "Scout's engine isn't set up yet.")
    }

    /// The sheet adds no inbox write path beyond review's. Every `scoutctl inbox` command goes through
    /// `InboxReviewService.runInbox`, whose `Command` allows review, accept, reject, promote and unpromote only:
    /// an `"inbox"` literal anywhere else in the app fails, and none of pull, apply, revert or sync appears in the
    /// service as a command.
    @Test func theAppNeverPullsAppliesRevertsOrSyncsAnInbox() throws {
        #expect(InboxReviewService.Command.allCases.map(\.rawValue) == ["review", "accept", "reject", "promote", "unpromote"])
        let source = URL(fileURLWithPath: #filePath)   // …/apps/macos/ScoutTests/Engine/InboxReviewTests.swift
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
        let files = try #require(FileManager.default.enumerator(at: source, includingPropertiesForKeys: nil))
            .compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" }
        #expect(files.count > 20)
        var inService = 0
        for file in files {
            let text = try String(contentsOf: file, encoding: .utf8)
            let count = text.components(separatedBy: #""inbox""#).count - 1
            if file.lastPathComponent == "InboxReviewService.swift" {
                inService = count
                for word in [#""pull""#, #""apply""#, #""revert""#, #""sync""#, "case pull", "case apply", "case revert",
                             "case sync"] {
                    #expect(!text.contains(word), "InboxReviewService.swift mentions \(word)")
                }
            } else {
                #expect(count == 0, "\(file.lastPathComponent) has an \"inbox\" literal outside InboxReviewService")
            }
            guard ["InboxReview.swift", "InboxReviewService.swift", "InboxReviewSheet.swift"]
                .contains(file.lastPathComponent) else { continue }
            for word in ["FileManager", "contentsOf", "delegate-inbox/state", ".textSelection", "AttributedString",
                         "LocalizedStringKey(", "Link("] {
                #expect(!text.contains(word), "\(file.lastPathComponent) mentions \(word)")
            }
        }
        #expect(inService == 1)   // runInbox's argv, and nothing else
    }
}
