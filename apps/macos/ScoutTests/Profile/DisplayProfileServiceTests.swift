import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("DisplayProfileService", .serialized)
struct DisplayProfileServiceTests {
    private let vault: URL
    private let fs = InjectableFS()

    init() throws {
        vault = FileManager.default.temporaryDirectory.appendingPathComponent("profile-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
    }

    private func makeService() -> DisplayProfileService {
        DisplayProfileService(scoutDirectory: vault, fileEvents: fs)
    }

    private func write(_ json: String, to service: DisplayProfileService) throws {
        try Data(json.utf8).write(to: service.fileURL, options: .atomic)
        service.reload()
    }

    private func onDisk(_ service: DisplayProfileService) throws -> NSDictionary? {
        guard let data = FileManager.default.contents(atPath: service.fileURL.path) else { return nil }
        return try JSONSerialization.jsonObject(with: data) as? NSDictionary
    }

    @Test func missingFileGivesDefaultsAndStaysMissing() throws {
        let service = makeService()
        #expect(service.profile == DisplayProfile())
        #expect(service.status == .ok(warnings: []))
        #expect(service.currentView == .list)
        #expect(!FileManager.default.fileExists(atPath: service.fileURL.path))
        #expect(service.fileURL.lastPathComponent == "scout-display.json")
        #expect(service.fileURL.deletingLastPathComponent().path == vault.resolvingRealPath().path)
    }

    @Test func firstChangeCreatesTheFile() throws {
        let service = makeService()
        service.update { $0.density = .compact }
        #expect(try onDisk(service) == ["schema": 1, "actionItems": ["density": "compact"]] as NSDictionary)
    }

    @Test func aNoOpChangeWritesNothing() {
        let service = makeService()
        service.update { $0.sort = .fileOrder }
        #expect(!FileManager.default.fileExists(atPath: service.fileURL.path))
    }

    @Test func switchingTheCurrentViewWritesNothing() {
        let service = makeService()
        service.currentView = .board
        #expect(service.profile.actionItems.defaultView == .list)
        #expect(!FileManager.default.fileExists(atPath: service.fileURL.path))
    }

    @Test func theTabOpensInTheDefaultViewAndChoosingADefaultShowsIt() throws {
        try Data(#"{"schema":1,"actionItems":{"defaultView":"board"}}"#.utf8)
            .write(to: vault.appendingPathComponent("scout-display.json"))
        let service = makeService()
        #expect(service.currentView == .board)
        service.update { $0.defaultView = .list }
        #expect(service.currentView == .list)
    }

    @Test func aHandEditIsPickedUpThroughTheWatcher() async throws {
        let service = makeService()
        service.startWatching()
        try Data(#"{"schema":1,"actionItems":{"density":"compact"}}"#.utf8).write(to: service.fileURL, options: .atomic)
        fs.emit(FileSystemEvent(url: service.fileURL, kind: .modified))
        for _ in 0..<40 where service.profile.actionItems.density != .compact {
            try await Task.sleep(nanoseconds: 25_000_000)
        }
        #expect(service.profile.actionItems.density == .compact)
    }

    @Test func eventsForOtherFilesAreIgnored() async throws {
        let service = makeService()
        service.startWatching()
        try Data(#"{"schema":1,"actionItems":{"density":"compact"}}"#.utf8).write(to: service.fileURL, options: .atomic)
        fs.emit(FileSystemEvent(url: service.fileURL.deletingLastPathComponent().appendingPathComponent("inbox.md"), kind: .modified))
        try await Task.sleep(nanoseconds: 150_000_000)
        #expect(service.profile.actionItems.density == .comfortable)
    }

    @Test func aBrokenFileKeepsTheLastGoodProfileAndHoldsWrites() throws {
        let service = makeService()
        try write(#"{"schema":1,"actionItems":{"sort":"alphabetical"}}"#, to: service)
        try write("{ broken", to: service)
        #expect(service.profile.actionItems.sort == .alphabetical)
        #expect(service.status == .unreadable(reason: "not valid JSON"))

        service.update { $0.density = .compact }
        #expect(service.profile.actionItems.density == .compact)          // applies for this session
        #expect(try String(contentsOf: service.fileURL, encoding: .utf8) == "{ broken")   // never overwritten
    }

    @Test func aBrokenFileAtLaunchGivesDefaults() throws {
        try Data("{ broken".utf8).write(to: vault.appendingPathComponent("scout-display.json"))
        let service = makeService()
        #expect(service.profile == DisplayProfile())
        #expect(!service.status.canWrite)
    }

    @Test func aNewerSchemaIsNeverOverwritten() throws {
        let newer = #"{"schema":2,"actionItems":{"defaultView":"board"}}"#
        try Data(newer.utf8).write(to: vault.appendingPathComponent("scout-display.json"))
        let service = makeService()
        #expect(service.status == .unsupportedSchema(found: 2))
        service.update { $0.density = .compact }
        #expect(try String(contentsOf: service.fileURL, encoding: .utf8) == newer)
    }

    @Test func fixingTheFileResumesWrites() throws {
        let service = makeService()
        try write("{ broken", to: service)
        try write(#"{"schema":1}"#, to: service)
        #expect(service.status == .ok(warnings: []))
        service.update { $0.grouping = .none }
        #expect(try onDisk(service) == ["schema": 1, "actionItems": ["group": "none"]] as NSDictionary)
    }

    @Test func warningsReachTheStatus() throws {
        let service = makeService()
        try write(#"{"schema":1,"actionItems":{"foo":true}}"#, to: service)
        #expect(service.status == .ok(warnings: [#"unknown key "actionItems.foo""#]))
        #expect(service.status.canWrite)
    }

    @Test func deletingTheFileResetsToDefaults() throws {
        let service = makeService()
        try write(#"{"schema":1,"actionItems":{"density":"compact"}}"#, to: service)
        try FileManager.default.removeItem(at: service.fileURL)
        service.reload()
        #expect(service.profile == DisplayProfile())
    }

    // MARK: - The Topics board's old Done range (audit #46)

    private func legacyDefaults(_ value: String?) -> UserDefaults {
        let suite = "r9-done-range-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        if let value { defaults.set(value, forKey: DisplayProfileService.legacyDoneRangeKey) }
        return defaults
    }

    @Test func aLegacyDoneRangeMovesIntoTheProfileOnce() throws {
        let service = makeService()
        let defaults = legacyDefaults("yesterday")
        service.migrateLegacyDoneRange(from: defaults)
        #expect(service.profile.actionItems.topics.doneRange == .yesterday)
        #expect(try onDisk(service) == ["schema": 1, "actionItems": ["topics": ["doneRange": "yesterday"]]] as NSDictionary)
        #expect(defaults.object(forKey: DisplayProfileService.legacyDoneRangeKey) == nil)
    }

    @Test func aRangeChosenInSettingsWinsAndTheOldKeyGoes() throws {
        try Data(#"{"schema":1,"actionItems":{"topics":{"doneRange":"today"}}}"#.utf8).write(to: vault.appendingPathComponent("scout-display.json"))
        let service = makeService()
        let defaults = legacyDefaults("yesterday")
        service.migrateLegacyDoneRange(from: defaults)
        #expect(service.profile.actionItems.topics.doneRange == .today)
        #expect(defaults.object(forKey: DisplayProfileService.legacyDoneRangeKey) == nil)
    }

    @Test func theDefaultOrAnUnknownLegacyValueWritesNothing() {
        for value in ["week", "fortnight"] {
            let service = makeService()
            let defaults = legacyDefaults(value)
            service.migrateLegacyDoneRange(from: defaults)
            #expect(!FileManager.default.fileExists(atPath: service.fileURL.path))
            #expect(defaults.object(forKey: DisplayProfileService.legacyDoneRangeKey) == nil)
        }
    }

    /// A write that fails on I/O (here: a read-only folder) leaves `status`
    /// .ok and sets `writeError`; the old key must survive for the next try.
    @Test func aFailedWriteKeepsTheOldKeyForLater() throws {
        let service = makeService()
        let folder = service.fileURL.deletingLastPathComponent()
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: folder.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: folder.path) }
        let defaults = legacyDefaults("yesterday")
        service.migrateLegacyDoneRange(from: defaults)
        #expect(service.status.canWrite)
        #expect(service.writeError != nil)
        #expect(defaults.string(forKey: DisplayProfileService.legacyDoneRangeKey) == "yesterday")
    }

    @Test func anUnwritableFileKeepsTheOldKeyForLater() throws {
        try Data("not json".utf8).write(to: vault.appendingPathComponent("scout-display.json"))
        let service = makeService()
        let defaults = legacyDefaults("today")
        service.migrateLegacyDoneRange(from: defaults)
        #expect(defaults.string(forKey: DisplayProfileService.legacyDoneRangeKey) == "today")
    }
}
