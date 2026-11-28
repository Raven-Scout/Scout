import Foundation
import Testing
@testable import Scout

/// The Sessions page's data source is injectable (UI audit C6): tests, the
/// big-vault fixture and a dev build on a synthetic vault read a fixed file,
/// never `scoutctl session index` and so never the Mac's real `~/.claude`.
@MainActor
@Suite("Sessions — index source")
struct SessionIndexSourceTests {

    private func tempFile(_ data: Data) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("sessions-source-\(UUID().uuidString).json")
        try data.write(to: url)
        return url
    }

    private func service(source: any SessionIndexSource, runner: RuleBasedRunner) -> SessionIndexService {
        SessionIndexService(configuration: .init(
            scoutctl: URL(fileURLWithPath: "/usr/bin/false"),
            argumentsPrefix: [],
            runner: runner,
            fileEvents: NoopFS(),
            indexFile: FileManager.default.temporaryDirectory
                .appendingPathComponent("no-such-\(UUID().uuidString)/sessions-index.json"),
            watchRoots: [],
            environment: [:],
            source: source
        ))
    }

    @Test("a file source publishes the file's index and runs no process")
    func fileSourcePublishesWithoutAProcess() async throws {
        let file = try tempFile(try SessionsFixture.data())
        defer { try? FileManager.default.removeItem(at: file) }
        let runner = RuleBasedRunner()
        let service = service(source: FileSessionIndexSource(file: file), runner: runner)

        await service.refreshFast()
        await service.refreshPRs()

        #expect(service.index?.sessions.count == (try SessionsFixture.index()).sessions.count)
        #expect(service.availability == .ok)
        #expect(service.lastError == nil)
        #expect(runner.calls.isEmpty)
    }

    @Test("a missing fixture file says so, and isn't reported as a missing engine")
    func missingFileIsAFailure() async throws {
        let runner = RuleBasedRunner()
        let missing = FileManager.default.temporaryDirectory.appendingPathComponent("absent-\(UUID().uuidString).json")
        let service = service(source: FileSessionIndexSource(file: missing), runner: runner)

        await service.refreshFast()

        #expect(service.index == nil)
        #expect(service.lastError?.contains("fixture file not found") == true)
        #expect(service.availability == .ok)
        #expect(runner.calls.isEmpty)
    }

    @Test("with no source the service still asks scoutctl, for both lanes")
    func defaultSourceIsScoutctl() async throws {
        let runner = RuleBasedRunner()
        runner.on(tool: "false", prefix: ["session", "index"], stdout: String(decoding: try SessionsFixture.data(), as: UTF8.self))
        let service = SessionIndexService(configuration: .init(
            scoutctl: URL(fileURLWithPath: "/usr/bin/false"),
            argumentsPrefix: [],
            runner: runner,
            fileEvents: NoopFS(),
            indexFile: FileManager.default.temporaryDirectory
                .appendingPathComponent("no-such-\(UUID().uuidString)/sessions-index.json"),
            watchRoots: [],
            environment: [:]
        ))

        await service.refreshFast()
        await service.refreshPRs()

        #expect(service.index != nil)
        #expect(runner.calls(to: "false").contains(["session", "index", "--json", "--no-gh"]))
        #expect(runner.calls(to: "false").contains(["session", "index", "--json"]))
    }

    @Test("the sessionsIndexFile default selects a file source, tilde expanded")
    func configuredFromDefaults() throws {
        let suite = "scout.tests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }

        #expect(FileSessionIndexSource.configured(in: defaults) == nil)
        defaults.set("   ", forKey: FileSessionIndexSource.defaultsKey)
        #expect(FileSessionIndexSource.configured(in: defaults) == nil)
        defaults.set("~/fixtures/sessions.json", forKey: FileSessionIndexSource.defaultsKey)
        let source = try #require(FileSessionIndexSource.configured(in: defaults))
        #expect(source.file.path == (("~/fixtures/sessions.json") as NSString).expandingTildeInPath)
    }

    @Test("a test AppState reads its sessions from the injected source")
    func appStateWiresTheSource() async throws {
        let vault = try BigVault(scale: BigVault.Scale(actionItems: 4, kbNotes: 4, sessions: 30, projects: 3,
                                                       research: 1, runDays: 1,
                                                       connectors: 2, slots: 2))
        defer { vault.tearDown() }

        await vault.state.sessionIndexService.refreshFast()

        #expect(vault.state.sessionIndexService.index?.sessions.count == 30)
        #expect(vault.runner.calls.allSatisfy { !$0.arguments.contains("session") })
    }
}
