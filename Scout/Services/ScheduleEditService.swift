import Foundation
import Combine

@MainActor
final class ScheduleEditService: ObservableObject {
    @Published private(set) var slots: [Slot] = []
    @Published private(set) var loadedMtime: Date?

    private let scoutctl: URL
    private let runner: any ProcessRunner
    private let argumentsPrefix: [String]
    let canonicalSchedulePath: URL

    init(
        scoutctl: URL,
        runner: any ProcessRunner,
        canonicalSchedulePath: URL,
        argumentsPrefix: [String] = []
    ) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
        self.canonicalSchedulePath = canonicalSchedulePath
    }

    /// Reads the live schedule via `scoutctl schedule list --json`, decodes,
    /// publishes. Captures the canonical file's mtime for the stale-check
    /// performed by save().
    func loadAll() async throws {
        let result = try await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + ["schedule", "list", "--json"],
            environment: [:],
            workingDirectory: nil
        )
        let decoded = try JSONDecoder().decode([Slot].self, from: result.stdout)
        self.slots = decoded
        self.loadedMtime = (try? FileManager.default
            .attributesOfItem(atPath: canonicalSchedulePath.path)[.modificationDate]) as? Date
    }
}
