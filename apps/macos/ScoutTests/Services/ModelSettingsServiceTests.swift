import Testing
import Foundation
@testable import Scout

/// Canned `scoutctl model show --json`. Mirrors the payload the engine's
/// `test_show_json_lists_default_and_every_slot` asserts — if these key names
/// drift apart, the pickers silently disappear.
private let showJSON = """
{"default": {"model": "opus", "source": "builtin"},
 "slots": [{"key": "midday-consolidation", "model": "sonnet", "source": "slot"},
           {"key": "morning-briefing", "model": "opus", "source": "builtin"}],
 "aliases": ["opus", "sonnet", "haiku"]}
"""

private let showAfterSetJSON = """
{"default": {"model": "haiku", "source": "config"},
 "slots": [{"key": "midday-consolidation", "model": "sonnet", "source": "slot"},
           {"key": "morning-briefing", "model": "haiku", "source": "config"}],
 "aliases": ["opus", "sonnet", "haiku"]}
"""

private let setJSON = #"{"config_path": "/Users/alex/Scout/scout-config.yaml", "default": "haiku", "changed": true}"#

private actor StubModelRunner: ProcessRunner {
    private(set) var calls: [[String]] = []
    private var stdouts: [String]
    private let exitCode: Int32
    private let stderr: String

    init(stdouts: [String], exitCode: Int32 = 0, stderr: String = "") {
        self.stdouts = stdouts
        self.exitCode = exitCode
        self.stderr = stderr
    }

    func recordedCalls() -> [[String]] { calls }

    nonisolated func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) async throws -> ProcessResult {
        let out = await next(arguments)
        return ProcessResult(exitCode: exitCode, stdout: Data(out.utf8), stderr: Data(stderr.utf8))
    }

    private func next(_ arguments: [String]) -> String {
        calls.append(arguments)
        if stdouts.count > 1 { return stdouts.removeFirst() }
        return stdouts.first ?? ""
    }
}

@MainActor
private func makeService(
    stdouts: [String], exitCode: Int32 = 0, stderr: String = ""
) -> (ModelSettingsService, StubModelRunner) {
    let runner = StubModelRunner(stdouts: stdouts, exitCode: exitCode, stderr: stderr)
    let service = ModelSettingsService(
        scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
        runner: runner,
        argumentsPrefix: ["--prefix"]
    )
    return (service, runner)
}

@Suite("ModelSettingsService")
struct ModelSettingsServiceTests {

    @Test("load decodes scoutctl model show --json")
    @MainActor
    func loadDecodes() async throws {
        let (service, runner) = makeService(stdouts: [showJSON])
        try await service.load()
        let settings = try #require(service.settings)
        #expect(settings.default == .init(model: "opus", source: "builtin"))
        #expect(settings.defaultIsBuiltIn)
        #expect(settings.slots.map(\.key) == ["midday-consolidation", "morning-briefing"])
        #expect(await runner.recordedCalls() == [["--prefix", "model", "show", "--json"]])
    }

    @Test("setDefault writes through the engine, then re-reads")
    @MainActor
    func setDefaultWritesThenReloads() async throws {
        let (service, runner) = makeService(stdouts: [setJSON, showAfterSetJSON])
        try await service.setDefault("haiku")
        #expect(service.settings?.default == .init(model: "haiku", source: "config"))
        #expect(await runner.recordedCalls() == [
            ["--prefix", "model", "set-default", "haiku", "--json"],
            ["--prefix", "model", "show", "--json"],
        ])
    }

    @Test("an engine without `scoutctl model` leaves settings nil and surfaces stderr")
    @MainActor
    func olderEngineFails() async {
        let (service, _) = makeService(stdouts: [""], exitCode: 2, stderr: "No such command 'model'.")
        await #expect(throws: (any Error).self) { try await service.load() }
        #expect(service.settings == nil)
        do {
            try await service.load()
        } catch {
            #expect(error.localizedDescription.contains("No such command"))
        }
    }
}
