import Testing
import Foundation
@testable import Scout

/// Verbatim `scoutctl kb classify --reader owner --json --repo <vault> <paths…>`
/// over the engine's privacy fixture vault (plugin/engine/tests/fixtures/privacy-vault).
/// If these key names drift from the engine's `Label.to_json`, the lock badge
/// silently disappears.
private let classifyJSON = """
[
  {
    "path": "knowledge-base/people/sam.md",
    "visibility": "private",
    "visibility_source": "frontmatter",
    "federation_scope": "none",
    "federation_scope_declared": "none",
    "federation_source": "reserved",
    "local_only": false,
    "shared_file": false,
    "warnings": []
  },
  {
    "path": "knowledge-base/topics/misc/plain.md",
    "visibility": "normal",
    "visibility_source": "default",
    "federation_scope": "none",
    "federation_scope_declared": "none",
    "federation_source": "reserved",
    "local_only": false,
    "shared_file": false,
    "warnings": []
  },
  {
    "path": "trading/positions.md",
    "visibility": "normal",
    "visibility_source": "default",
    "federation_scope": "none",
    "federation_scope_declared": "none",
    "federation_source": "reserved",
    "local_only": true,
    "shared_file": false,
    "warnings": []
  }
]
"""

/// A declared principal list and an agent reader's denial row.
private let oddRowsJSON = """
[
  {"path": "drafts/reply-to-priya.md", "visibility": "normal", "visibility_source": "default",
   "federation_scope": "none", "federation_scope_declared": ["person:priya@example.com"],
   "federation_source": "reserved", "local_only": false, "shared_file": true, "warnings": []},
  {"path": "knowledge-base/people/sam.md", "denied": true}
]
"""

private actor StubClassifyRunner: ProcessRunner {
    private(set) var calls: [[String]] = []
    private let respond: @Sendable ([String]) -> ProcessResult

    init(respond: @escaping @Sendable ([String]) -> ProcessResult) {
        self.respond = respond
    }

    func recordedCalls() -> [[String]] { calls }

    nonisolated func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) async throws -> ProcessResult {
        await record(arguments)
        return respond(arguments)
    }

    private func record(_ arguments: [String]) { calls.append(arguments) }
}

/// Labels for whatever paths the call names: private when the path has "personal/" in it.
private func echoLabels(_ arguments: [String]) -> ProcessResult {
    let paths = Array(arguments.drop(while: { $0 != "--" }).dropFirst())
    let rows = paths.map { p in
        #"{"path": "\#(p)", "visibility": "\#(p.contains("personal/") ? "private" : "normal")"}"#
    }
    return ProcessResult(exitCode: 0, stdout: Data("[\(rows.joined(separator: ","))]".utf8), stderr: Data())
}

private func file(_ rel: String) -> KBNode {
    KBNode(kind: .file, url: URL(fileURLWithPath: "/vault/\(rel)"), relativePath: rel,
           name: (rel as NSString).lastPathComponent, ext: "md", children: [])
}

private func dir(_ rel: String, _ children: [KBNode]) -> KBNode {
    KBNode(kind: .directory, url: URL(fileURLWithPath: "/vault/\(rel)"), relativePath: rel,
           name: (rel as NSString).lastPathComponent, ext: "", children: children)
}

private final class StampBox: @unchecked Sendable {
    var stamps: [String: KBFileStamp] = [:]
    func stamp(_ url: URL) -> KBFileStamp? { stamps[url.path] }
}

private func stampOf(_ seconds: Double, size: Int64 = 10) -> KBFileStamp {
    KBFileStamp(modified: Date(timeIntervalSince1970: seconds), size: size, inode: 1)
}

@Suite("KB privacy labels")
struct KBPrivacyLabelsTests {

    @Test("decodes the engine's classify output")
    func decodesRealOutput() throws {
        let labels = try KBPrivacyLabel.decodeList(Data(classifyJSON.utf8))
        #expect(labels.map(\.path) == [
            "knowledge-base/people/sam.md", "knowledge-base/topics/misc/plain.md", "trading/positions.md",
        ])
        #expect(labels.map(\.visibility) == ["private", "normal", "normal"])
        #expect(labels.map(\.localOnly) == [false, false, true])
        #expect(labels.allSatisfy { $0.denied == nil })
    }

    @Test("decodes a principal list and a denial row")
    func decodesOddRows() throws {
        let labels = try KBPrivacyLabel.decodeList(Data(oddRowsJSON.utf8))
        #expect(labels.count == 2)
        #expect(labels[1].denied == true && labels[1].visibility == nil)
    }

    @Test("label → badge: private locks; normal, denied and missing don't")
    func badgeMapping() throws {
        let labels = try KBPrivacyLabel.decodeList(Data(classifyJSON.utf8))
        #expect(labels.map(KBPrivacyBadge.badge(for:)) == [.locked, .none, .none])
        let denied = try KBPrivacyLabel.decodeList(Data(oddRowsJSON.utf8))[1]
        #expect(KBPrivacyBadge.badge(for: denied) == .none)
        #expect(KBPrivacyBadge.badge(for: nil) == .none)
        let odd = KBPrivacyLabel(path: "x.md", visibility: " PRIVATE ", localOnly: nil, denied: nil)
        #expect(KBPrivacyBadge.badge(for: odd) == .locked)
        let unknown = KBPrivacyLabel(path: "x.md", visibility: "secret", localOnly: nil, denied: nil)
        #expect(KBPrivacyBadge.badge(for: unknown) == .locked)
        #expect(KBPrivacyBadge.locked.systemImage == "lock.fill")
        #expect(KBPrivacyBadge.none.systemImage == nil)
    }

    @Test("classify arguments ask as the owner, in JSON, for the vault")
    func arguments() {
        let args = KBPrivacyLabelService.classifyArguments(
            vault: URL(fileURLWithPath: "/Users/alex/Scout"), paths: ["knowledge-base/a.md"])
        #expect(args == ["kb", "classify", "--reader", "owner", "--json", "--repo", "/Users/alex/Scout",
                         "--", "knowledge-base/a.md"])
    }

    @Test("only files under expanded folders are visible")
    func visibleFiles() {
        let tree = [
            dir("knowledge-base/personal", [file("knowledge-base/personal/t.md")]),
            dir("knowledge-base/people", [file("knowledge-base/people/sam.md")]),
            file("knowledge-base/index.md"),
        ]
        let visible = KBPrivacyLabelService.visibleFiles(in: tree, expanded: ["knowledge-base/personal"])
        #expect(visible.map(\.relativePath) == ["knowledge-base/personal/t.md", "knowledge-base/index.md"])
    }

    @Test("batches files, caches by stamp, and re-asks only for changed files")
    @MainActor
    func batchesAndCaches() async {
        let runner = StubClassifyRunner(respond: { echoLabels($0) })
        let stamps = StampBox()
        let files = (1...5).map { file("knowledge-base/\($0 == 3 ? "personal/" : "")n\($0).md") }
        for (i, f) in files.enumerated() { stamps.stamps[f.url.path] = stampOf(Double(i)) }
        let service = KBPrivacyLabelService(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"), runner: runner,
            vault: URL(fileURLWithPath: "/vault"), batchSize: 2, stamp: { stamps.stamp($0) })

        await service.refresh(files)
        #expect(service.privatePaths == ["knowledge-base/personal/n3.md"])
        #expect(await runner.recordedCalls().count == 3)  // 5 files in batches of 2

        await service.refresh(files)
        #expect(await runner.recordedCalls().count == 3)  // nothing changed: no process

        stamps.stamps[files[0].url.path] = stampOf(99)
        await service.refresh(files)
        let calls = await runner.recordedCalls()
        #expect(calls.count == 4)
        #expect(calls.last?.suffix(1) == ["knowledge-base/n1.md"])
    }

    @Test("a failing scoutctl shows no badge and doesn't throw")
    @MainActor
    func failureShowsNoBadge() async {
        let runner = StubClassifyRunner(respond: { _ in
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data("index locked".utf8))
        })
        let stamps = StampBox()
        let f = file("knowledge-base/personal/t.md")
        stamps.stamps[f.url.path] = stampOf(1)
        let service = KBPrivacyLabelService(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"), runner: runner,
            vault: URL(fileURLWithPath: "/vault"), stamp: { stamps.stamp($0) })
        await service.refresh([f])
        #expect(service.privatePaths.isEmpty)
        #expect(service.badge(for: f.relativePath) == .none)
        // Not cached: the next refresh asks again.
        await service.refresh([f])
        #expect(await runner.recordedCalls().count == 2)
    }

    @Test("an engine without kb classify isn't asked again until scoutctl changes")
    @MainActor
    func backsOffOnUnsupportedEngine() async {
        #expect(KBPrivacyLabelService.isUnsupported(exitCode: 2, stderr: ""))
        #expect(KBPrivacyLabelService.isUnsupported(exitCode: 1, stderr: "No such command 'classify'."))
        #expect(!KBPrivacyLabelService.isUnsupported(exitCode: 1, stderr: "index locked"))

        let runner = StubClassifyRunner(respond: { _ in
            ProcessResult(exitCode: 2, stdout: Data(), stderr: Data("No such command 'classify'.".utf8))
        })
        let stamps = StampBox()
        let scoutctl = URL(fileURLWithPath: "/usr/local/bin/scoutctl")
        stamps.stamps[scoutctl.path] = stampOf(1)
        let f = file("knowledge-base/personal/t.md")
        stamps.stamps[f.url.path] = stampOf(1)
        let service = KBPrivacyLabelService(
            scoutctl: scoutctl, runner: runner,
            vault: URL(fileURLWithPath: "/vault"), stamp: { stamps.stamp($0) })
        await service.refresh([f])
        await service.refresh([f])
        #expect(await runner.recordedCalls().count == 1)  // backed off
        stamps.stamps[scoutctl.path] = stampOf(2)  // the engine was updated
        await service.refresh([f])
        #expect(await runner.recordedCalls().count == 2)
    }

    @Test("a changed scout-config.yaml relabels every file")
    @MainActor
    func configChangeClearsCache() async {
        let runner = StubClassifyRunner(respond: { echoLabels($0) })
        let stamps = StampBox()
        let config = URL(fileURLWithPath: "/vault/scout-config.yaml")
        stamps.stamps[config.path] = stampOf(1)
        let files = [file("knowledge-base/a.md"), file("knowledge-base/personal/b.md")]
        for f in files { stamps.stamps[f.url.path] = stampOf(1) }
        let service = KBPrivacyLabelService(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"), runner: runner,
            vault: URL(fileURLWithPath: "/vault"), stamp: { stamps.stamp($0) })
        await service.refresh(files)
        await service.refresh(files)
        #expect(await runner.recordedCalls().count == 1)
        stamps.stamps[config.path] = stampOf(2)  // e.g. a new privacy.local_only_add or visibility default
        await service.refresh(files)
        let calls = await runner.recordedCalls()
        #expect(calls.count == 2)
        #expect(calls.last?.suffix(2) == ["knowledge-base/a.md", "knowledge-base/personal/b.md"])
        #expect(service.privatePaths == ["knowledge-base/personal/b.md"])
    }

    @Test("garbage output shows no badge")
    @MainActor
    func garbageShowsNoBadge() async {
        let runner = StubClassifyRunner(respond: { _ in
            ProcessResult(exitCode: 0, stdout: Data("not json".utf8), stderr: Data())
        })
        let stamps = StampBox()
        let f = file("knowledge-base/personal/t.md")
        stamps.stamps[f.url.path] = stampOf(1)
        let service = KBPrivacyLabelService(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"), runner: runner,
            vault: URL(fileURLWithPath: "/vault"), stamp: { stamps.stamp($0) })
        await service.refresh([f])
        #expect(service.privatePaths.isEmpty)
    }
}
