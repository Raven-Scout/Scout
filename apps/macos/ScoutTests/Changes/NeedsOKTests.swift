import Foundation
import SwiftUI
import Testing
@testable import Scout

@MainActor
@Suite("Needs your OK", .serialized)
struct NeedsOKTests {
    private static let hash = "062c40aea93c47c1d4ec600bb08634139853c2c6b0d094d9b5a5882c6f7c0a1b"
    private static let pendingFile = """
        ---
        id: gov-skip-carried-red
        ---
        Why this matters.

        ```diff
        --- a/briefing.md
        +++ b/briefing.md
        @@ -1 +1 @@
        -old
        +new
        ```
        """

    private func vault(file: String = pendingFile, name: String = "skip-carried-red.md") throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("needs-ok-\(UUID().uuidString)")
        let pending = root.appendingPathComponent("governance-pending")
        try FileManager.default.createDirectory(at: pending, withIntermediateDirectories: true)
        try file.write(to: pending.appendingPathComponent(name), atomically: true, encoding: .utf8)
        return root
    }

    private func item(path: String = "governance-pending/skip-carried-red.md") -> NeedsOKItem {
        let json = """
        {"id":"gov-skip-carried-red","summary":"Let the briefing drop a red item","why":"Carried six days.",
         "path":"\(path)","state":"awaiting-ok","diff_hash":"\(Self.hash)","targets":["briefing"],"governance":true}
        """
        return try! JSONDecoder().decode(NeedsOKItem.self, from: Data(json.utf8))
    }

    /// A service whose changes reads are counted, and a decisions object on the same runner.
    private func rig(approveStderr: String? = nil, approveExit: Int32 = 0) -> (RuleBasedRunner, ChangesService, ChangesDecisions) {
        let runner = RuleBasedRunner()
        func fixture(_ name: String) -> String { String(decoding: try! ChangesContractTests.data(name), as: UTF8.self) }
        runner.on(tool: "scoutctl", prefix: ["changes", "digest"], stdout: fixture("changes-digest"))
        runner.on(tool: "scoutctl", prefix: ["changes", "--json"], stdout: fixture("changes"))
        runner.on(tool: "scoutctl", prefix: ["engagement", "--json"], stdout: fixture("engagement"))
        runner.on(tool: "scoutctl", prefix: ["changes", "approve"], stderr: approveStderr ?? "", exit: approveExit)
        runner.on(tool: "scoutctl", prefix: ["changes", "reject"])
        let exe = URL(fileURLWithPath: "/usr/bin/scoutctl")
        let service = ChangesService(
            scoutctl: { exe }, runner: runner,
            logsDirectory: URL(fileURLWithPath: "/tmp/none-\(UUID().uuidString)"),
            pendingDirectory: URL(fileURLWithPath: "/tmp/none-\(UUID().uuidString)"), fileEvents: NoopFS())
        return (runner, service, ChangesDecisions(runner: runner, scoutctl: { exe }))
    }

    private func refreshCount(_ runner: RuleBasedRunner) -> Int {
        runner.calls(to: "scoutctl").filter { $0.prefix(2) == ["changes", "--json"] }.count
    }

    // MARK: - The strip

    @Test func stripHiddenWithNothingAwaiting() async throws {
        let service = try await LoadedChangesService.make()
        let all = try #require(service.snapshot).changes.needsOK
        #expect(all.count == 2)
        #expect(NeedsOKStrip.awaiting(all).map(\.id) == ["gov-skip-carried-red"])   // the approved one is not shown
        #expect(NeedsOKStrip.awaiting(all.filter { $0.state != "awaiting-ok" }).isEmpty)
        #expect(NeedsOKStrip.awaiting([]).isEmpty)
        let strip = NeedsOKStrip(items: all.filter { $0.state != "awaiting-ok" }, vaultDirectory: nil,
                                 decisions: LoadedChangesService.decisions(), service: service)
        let nodes = await AccessibilityTree.nodes(of: strip.frame(width: 700), size: CGSize(width: 700, height: 400))
        #expect(!nodes.contains { $0.label.contains("Needs your OK") })
    }

    @Test func stripShowsSummaryAndWhyNeverTheId() async throws {
        let service = try await LoadedChangesService.make()
        let all = try #require(service.snapshot).changes.needsOK
        let strip = NeedsOKStrip(items: all, vaultDirectory: nil, decisions: LoadedChangesService.decisions(), service: service)
        let nodes = await AccessibilityTree.nodes(of: strip.frame(width: 700), size: CGSize(width: 700, height: 400))
        let shown = nodes.flatMap { [$0.label, $0.value] }.filter { !$0.isEmpty }
        let text = shown.joined(separator: "\n")
        #expect(text.contains("Needs your OK (1)"), "\(text)")
        #expect(text.contains("Let the briefing drop a red item nobody answered for six days"))
        for line in shown { #expect(!line.contains("gov-"), "bare id in: \(line)") }
    }

    // MARK: - Decisions

    @Test func approveSendsTheHashTheOwnerSaw() async throws {
        let (runner, service, decisions) = rig()
        let model = GovernanceReviewModel(item: item(), vaultDirectory: try vault(), decisions: decisions, service: service)
        #expect(model.diff != nil)
        #expect(model.canApprove)
        await model.approve()
        #expect(runner.calls(to: "scoutctl").contains(
            ["changes", "approve", "gov-skip-carried-red", "--diff-hash", Self.hash, "--surface", "app"]))
        #expect(model.finished)
        #expect(refreshCount(runner) == 1)
    }

    @Test func approveAfterTheFileWasRewrittenSendsNothing() async throws {
        let (runner, service, decisions) = rig()
        let root = try vault()
        let model = GovernanceReviewModel(item: item(), vaultDirectory: root, decisions: decisions, service: service)
        #expect(model.canApprove)
        // A→B under the open sheet: the owner saw A, the file now says B.
        try Self.pendingFile.replacingOccurrences(of: "+new", with: "+other").write(
            to: root.appendingPathComponent("governance-pending/skip-carried-red.md"), atomically: true, encoding: .utf8)
        await model.approve()
        #expect(runner.calls(to: "scoutctl").filter { $0.prefix(2) == ["changes", "approve"] }.isEmpty)
        #expect(model.message == GovernanceReviewModel.editedMessage)
        #expect(!model.finished)
    }

    @Test func aFinalSymlinkIsNotRead() throws {
        let root = try vault()
        let link = root.appendingPathComponent("governance-pending/inside.md")
        try FileManager.default.createSymbolicLink(
            at: link, withDestinationURL: root.appendingPathComponent("governance-pending/skip-carried-red.md"))
        #expect(GovernanceReviewModel.readDiff(vault: root, path: "governance-pending/inside.md") == nil)
    }

    @Test func rejectRunsAndRefreshesOnce() async throws {
        let (runner, service, decisions) = rig()
        let model = GovernanceReviewModel(item: item(), vaultDirectory: try vault(), decisions: decisions, service: service)
        await model.reject()
        #expect(runner.calls(to: "scoutctl").contains(["changes", "reject", "gov-skip-carried-red", "--surface", "app"]))
        #expect(model.finished)
        #expect(refreshCount(runner) == 1)
    }

    @Test func approveRefusedAfterEditReloads() async throws {
        let (runner, service, decisions) = rig(
            approveStderr: "scoutctl changes approve: gov-skip-carried-red changed since you saw it; review it again\n",
            approveExit: 1)
        let model = GovernanceReviewModel(item: item(), vaultDirectory: try vault(), decisions: decisions, service: service)
        await model.approve()
        #expect(model.message == "This change was edited after you opened it. Review it again.")
        #expect(!model.finished)
        #expect(refreshCount(runner) == 1)
    }

    @Test func otherRefusalsShowTheirTextAndDoNotRefresh() async throws {
        let (runner, service, decisions) = rig(approveStderr: "scoutctl changes approve: a scheduled run may not make it\n", approveExit: 2)
        let model = GovernanceReviewModel(item: item(), vaultDirectory: try vault(), decisions: decisions, service: service)
        await model.approve()
        #expect(model.message == "Scout is running right now. Try again when the run finishes.")
        #expect(refreshCount(runner) == 0)
        #expect(!model.finished)
    }

    // MARK: - The diff

    @Test func diffBlockFindsTheFence() {
        let block = GovernanceReviewModel.diffBlock(fromPendingFile: Self.pendingFile)
        #expect(block == "--- a/briefing.md\n+++ b/briefing.md\n@@ -1 +1 @@\n-old\n+new\n")
    }

    @Test func diffBlockIsNilWithoutOne() {
        #expect(GovernanceReviewModel.diffBlock(fromPendingFile: "no fence here") == nil)
        #expect(GovernanceReviewModel.diffBlock(fromPendingFile: "```swift\nlet x = 1\n```\n") == nil)
        #expect(GovernanceReviewModel.diffBlock(fromPendingFile: "```diff\nnever closed\n") == nil)
        let two = "```diff\n-a\n+b\n```\ntext\n```diff\n-c\n+d\n```\n"
        #expect(GovernanceReviewModel.diffBlock(fromPendingFile: two) == nil)   // the engine reads exactly one
    }

    @Test func approveIsOffWhenNoDiffCouldBeRead() throws {
        let (_, service, decisions) = rig()
        let noFence = try vault(file: "just prose, no diff block\n")
        let model = GovernanceReviewModel(item: item(), vaultDirectory: noFence, decisions: decisions, service: service)
        #expect(model.diff == nil)
        #expect(!model.canApprove)
        #expect(model.canReject)
        #expect(model.diffMissingMessage == "Scout couldn't show this change's diff.")
        let missing = GovernanceReviewModel(item: item(), vaultDirectory: nil, decisions: decisions, service: service)
        #expect(!missing.canApprove)
    }

    // MARK: - The path is engine input

    @Test func aPathWithDotDotIsRefused() throws {
        let root = try vault()
        // A real file outside the pending folder that a `..` path would reach.
        try "```diff\n-a\n+b\n```\n".write(to: root.appendingPathComponent("outside.md"), atomically: true, encoding: .utf8)
        #expect(GovernanceReviewModel.pendingFileURL(vault: root, path: "governance-pending/skip-carried-red.md") != nil)
        for bad in ["governance-pending/../outside.md", "../outside.md", "governance-pending/..", "..", "/etc/passwd",
                    "~/x.md", "governance-pending//skip-carried-red.md", "", "outside.md", "governance-pending/"] {
            #expect(GovernanceReviewModel.pendingFileURL(vault: root, path: bad) == nil, "\(bad)")
            #expect(GovernanceReviewModel.readDiff(vault: root, path: bad) == nil, "\(bad)")
        }
    }

    @Test func aSymlinkOutOfThePendingFolderIsRefused() throws {
        let root = try vault()
        let target = root.appendingPathComponent("outside.md")
        try "```diff\n-a\n+b\n```\n".write(to: target, atomically: true, encoding: .utf8)
        try FileManager.default.createSymbolicLink(
            at: root.appendingPathComponent("governance-pending/link.md"), withDestinationURL: target)
        #expect(GovernanceReviewModel.pendingFileURL(vault: root, path: "governance-pending/link.md") == nil)
        #expect(GovernanceReviewModel.readDiff(vault: root, path: "governance-pending/link.md") == nil)
    }
}
