import Foundation
import Testing
@testable import Scout

/// Settings ▸ Agent copies (#550): the `scoutctl delegate status --json` contract, its
/// plain-language model, and the only commands the card runs.
///
/// `delegate-status.json` is a byte copy of the engine's generated
/// `plugin/engine/tests/fixtures/contract/delegate-status.json` (root CLAUDE.md). When it is
/// regenerated, update the pins in `decodesTheSharedFixture`.
@Suite("Agent copies")
@MainActor
struct DelegateStatusTests {
    private final class FixtureAnchor {}

    static func sharedFixture() throws -> Data {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: "delegate-status", withExtension: "json"))
        return try Data(contentsOf: url)
    }

    static func fixtureStatus() throws -> DelegateStatus {
        try #require(DelegateStatus.parse(try sharedFixture()))
    }

    /// Twelve minutes after the fixture's last good sync (2026-11-19T11:45:00Z).
    private let now = ISO8601DateFormatter().date(from: "2026-11-19T11:57:00Z")!

    // MARK: - The contract

    @Test func decodesTheSharedFixture() throws {
        let s = try Self.fixtureStatus()
        #expect(s.schemaVersion == 1)
        #expect(s.state == "ok")
        #expect(s.owner)
        #expect(s.skipped == 0)
        #expect(s.delegates.map(\.id) == ["grok-personal", "duet", "research-bot"])
        let grok = s.delegates[0], duet = s.delegates[1], bot = s.delegates[2]
        #expect(grok.remote == .init(kind: "github", display: "example-org/grok-mirror"))
        #expect(grok.lastResult == "ok" && grok.lastSync == "2026-11-19T11:45:00Z" && grok.behind == false)
        #expect(grok.counts == .init(files: 412, withheld: 37, leftOut: 3, redactedLines: 9, agentConfig: 2))
        #expect(grok.agentConfigLeftOut == 2 && grok.deployKey == true && grok.oldKeysLive == 0)
        #expect(grok.canSync && grok.canPreview && !grok.stateUnverified)
        #expect(duet.lastResult == "failed" && duet.behind == true)
        #expect(duet.refusal?.code == "mirror_diverged")
        #expect(duet.refusal?.since == "2026-11-19T08:00:00Z")
        #expect(duet.remote == .init(kind: "local", display: "~/mirrors/duet.git"))
        #expect(duet.oldKeysLive == 1)
        #expect(!bot.linked && bot.listed && bot.remote == nil && bot.counts == nil)
        #expect(bot.canPreview && !bot.canSync)
    }

    @Test func aNewerSchemaIsRefusedNotHalfRead() throws {
        var obj = try #require(try JSONSerialization.jsonObject(with: try Self.sharedFixture()) as? [String: Any])
        obj["schema_version"] = 2
        #expect(DelegateStatus.parse(try JSONSerialization.data(withJSONObject: obj)) == nil)
    }

    @Test func theOldUnversionedJSONIsNotRead() {
        // What `delegate status --json` printed before the card: raw link state.
        #expect(DelegateStatus.parse(Data(#"{"ok": true, "links": []}"#.utf8)) == nil)
        #expect(DelegateStatus.parse(Data("not json".utf8)) == nil)
    }

    @Test func delegateIDsAreCheckedBeforeTheyReachScoutctl() {
        #expect(DelegateStatus.isDelegateID("grok-personal"))
        for bad in ["", "-x", "Grok", "a b", "--all", "x/y", String(repeating: "a", count: 41)] {
            #expect(!DelegateStatus.isDelegateID(bad), "\(bad)")
        }
    }

    // MARK: - The plain-language model

    @Test func theFixtureReadsInPlainWords() throws {
        let m = AgentCopiesModel(status: try Self.fixtureStatus(), now: now)
        #expect(m.isVisible)
        #expect(m.stateProblem == nil)
        let rows = Dictionary(uniqueKeysWithValues: m.rows.map { ($0.id, $0) })
        let grok = try #require(rows["grok-personal"])
        #expect(grok.value == "Synced 12 minutes ago")
        #expect(grok.tone == .ok)
        #expect(grok.help.contains("GitHub: example-org/grok-mirror."))
        #expect(grok.help.contains("Holds 412 files; 37 withheld, 3 left out, 9 lines removed."))
        #expect(grok.help.contains("2 files left out as agent config"))
        #expect(grok.offersSync && grok.offersPreview)
        let duet = try #require(rows["duet"])
        #expect(duet.value == "Sync refused")
        #expect(duet.tone == .bad)
        #expect(duet.help.contains("The mirror repository moved since the last sync"))
        #expect(duet.help.contains("Failing since"))
        #expect(duet.help.contains("1 old deploy key is still live"))
        let bot = try #require(rows["research-bot"])
        #expect(bot.value == "Not linked")
        #expect(bot.help.contains("scoutctl delegate link research-bot"))
        #expect(bot.offersPreview && !bot.offersSync)
    }

    @Test func anUnsignedStateNamesTheFixAndOffersNoSync() throws {
        var obj = try #require(try JSONSerialization.jsonObject(with: try Self.sharedFixture()) as? [String: Any])
        obj["state"] = "unverified"
        var delegates = try #require(obj["delegates"] as? [[String: Any]])
        for i in delegates.indices where delegates[i]["linked"] as? Bool == true {
            delegates[i]["state_unverified"] = true
            delegates[i]["can_sync"] = false
        }
        obj["delegates"] = delegates
        let m = AgentCopiesModel(status: try #require(DelegateStatus.parse(try JSONSerialization.data(withJSONObject: obj))),
                                 now: now)
        #expect(m.stateProblem?.fix.contains("scoutctl delegate trust-state") == true)
        #expect(m.rows[0].value == "Not trusted")
        #expect(m.rows.allSatisfy { !$0.offersSync })
    }

    @Test func noDelegatesHidesTheCard() {
        let empty = DelegateStatus(schemaVersion: 1, state: "none", owner: true, skipped: 0, delegates: [])
        #expect(!AgentCopiesModel(status: empty).isVisible)
        let broken = DelegateStatus(schemaVersion: 1, state: "unreadable", owner: true, skipped: 0, delegates: [])
        #expect(AgentCopiesModel(status: broken).isVisible)
    }

    @Test func outcomesShowCountsOnly() {
        let counts = DelegateMirrorCounts(files: 10, withheld: 2, leftOut: 1, redactedLines: 0, agentConfig: 3, unchanged: nil)
        #expect(AgentCopiesModel.outcomeText(.preview(counts))
                == "Preview: would hold 10 files; 2 withheld, 1 left out. 3 files left out as agent config.")
        let same = DelegateMirrorCounts(files: 10, withheld: 2, leftOut: 0, redactedLines: 0, agentConfig: 0, unchanged: true)
        #expect(AgentCopiesModel.outcomeText(.synced(same)) == "Synced just now: already up to date.")
        #expect(AgentCopiesModel.outcomeText(.refused("mirror_diverged")) == "The sync just now was refused (mirror_diverged).")
    }

    // MARK: - The service

    private func service(_ runner: RuleBasedRunner, scoutctl: URL? = URL(fileURLWithPath: "/engine/bin/scoutctl")) -> DelegateStatusService {
        DelegateStatusService(runner: runner, environment: ["SCOUT_DATA_DIR": "/v"], scoutctl: { scoutctl })
    }

    private func runnerWithStatus() throws -> RuleBasedRunner {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["delegate", "status", "--json"],
                  stdout: String(decoding: try Self.sharedFixture(), as: UTF8.self))
        return runner
    }

    @Test func refreshOnlyReads() async throws {
        let runner = try runnerWithStatus()
        let svc = service(runner)
        await svc.refresh()
        #expect(svc.status?.delegates.count == 3)
        #expect(svc.lastError == nil && svc.work == nil)
        #expect(runner.calls.first?.environment["SCOUT_DATA_DIR"] == "/v")
        #expect(runner.calls(to: "scoutctl") == [["delegate", "status", "--json"]])
    }

    @Test func previewReadsOnlyTheCounts() async throws {
        let runner = try runnerWithStatus()
        // The real output also has notes and left-out file names; the app never decodes them.
        runner.on(tool: "scoutctl", prefix: ["delegate", "preview"], stdout: """
            {"ok": true, "delegate": "grok-personal", "action": "preview", "files": 400, "withheld": 30, "left_out": 2,
             "redacted_lines": 4, "redacted_files": 1, "agent_config": 1, "unchanged": false, "pushed": null,
             "in_history": 0, "left_out_files": {"notes/a.docx": "macros"}, "notes": ["left out: notes/a.docx"]}
            """)
        let svc = service(runner)
        await svc.refresh()
        await svc.preview("grok-personal")
        #expect(runner.calls(to: "scoutctl").dropFirst() == [["delegate", "preview", "grok-personal", "--json"],
                                                             ["delegate", "status", "--json"]])
        guard case .preview(let c) = svc.results["grok-personal"] else { Issue.record("no preview"); return }
        #expect(c.files == 400 && c.agentConfig == 1)
        let help = AgentCopiesModel(status: try #require(svc.status), results: svc.results, now: now).rows[0].help
        #expect(!help.contains("a.docx"))
    }

    @Test func syncRunsTheCLIsOwnSyncAndShowsARefusalByItsCode() async throws {
        let runner = try runnerWithStatus()
        runner.on(tool: "scoutctl", prefix: ["delegate", "sync"],
                  stdout: #"{"ok": false, "code": "mirror_diverged", "message": "refused: notes/secret.md moved"}"#, exit: 2)
        let svc = service(runner)
        await svc.refresh()
        await svc.sync("duet")
        #expect(runner.calls(to: "scoutctl").dropFirst() == [["delegate", "sync", "duet", "--json"], ["delegate", "status", "--json"]])
        #expect(svc.results["duet"] == .refused("mirror_diverged"))
        #expect(svc.lastError == nil)
    }

    @Test func noActionRunsForADelegateTheEngineDidNotOffer() async throws {
        let runner = try runnerWithStatus()
        let svc = service(runner)
        await svc.refresh()
        await svc.sync("research-bot")   // not linked: can_sync is false
        await svc.sync("--all")          // not an id at all
        await svc.preview("someone-else") // not in the status
        #expect(runner.calls(to: "scoutctl") == [["delegate", "status", "--json"]])
    }

    @Test func anEngineFromBeforeTheCardSaysToUpdate() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["delegate", "status"], stdout: #"{"ok": true, "links": []}"#)
        let svc = service(runner)
        await svc.refresh()
        #expect(svc.status == nil)
        #expect(svc.lastError?.contains("Update the engine") == true)
    }

    @Test func noEngineRunsNothing() async {
        let runner = RuleBasedRunner()
        let svc = service(runner, scoutctl: nil)
        await svc.refresh()
        #expect(runner.calls.isEmpty)
        #expect(svc.lastError == "Scout's engine isn't set up yet.")
    }

    /// The card adds no delegate write path: no app source runs a `scoutctl delegate` command but
    /// status, preview and sync, and the card's own code opens no state, key or token file.
    @Test func theAppNeverRunsLinkKeysOrTrustState() throws {
        let source = URL(fileURLWithPath: #filePath)   // …/apps/macos/ScoutTests/Engine/DelegateStatusTests.swift
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
        let files = try #require(FileManager.default.enumerator(at: source, includingPropertiesForKeys: nil))
            .compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" }
        #expect(files.count > 20)
        let delegateArgv = try Regex(#""delegate",\s*"([a-z-]+)""#)
        var seen = Set<String>()
        for file in files {
            let text = try String(contentsOf: file, encoding: .utf8)
            for match in text.matches(of: delegateArgv) {
                let sub = String(match.output[1].substring ?? "")
                seen.insert(sub)
                #expect(["status", "preview", "sync"].contains(sub), "\(file.lastPathComponent) runs scoutctl delegate \(sub)")
            }
            // The card's own code opens no state, key or token file. (DiagnosticsScrubber reads the state's
            // delegate ids to redact them from reports; that predates the card and is not a run path.)
            guard ["DelegateStatus.swift", "DelegateStatusService.swift", "AgentCopiesSection.swift"]
                .contains(file.lastPathComponent) else { continue }
            for word in ["delegate-mirrors", ".scout-secrets", "push-token", "FileManager", "contentsOf"] {
                #expect(!text.contains(word), "\(file.lastPathComponent) mentions \(word)")
            }
        }
        #expect(seen == ["status", "preview", "sync"])   // found the service's commands, so the scan works
    }
}
