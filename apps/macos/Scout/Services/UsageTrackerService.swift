import Foundation
import Combine
import SwiftUI

@MainActor
final class UsageTrackerService: ObservableObject {
    @Published private(set) var entries: [UsageEntry] = []

    private let trackerURL: URL
    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?

    init(trackerURL: URL, fileEvents: any FileSystemEventSource) {
        self.trackerURL = trackerURL
        self.fileEvents = fileEvents
    }

    func loadInitial() async throws -> [UsageEntry] {
        let parsed = parseFile(trackerURL)
        let filtered = parsed.filter { ($0.source ?? "session") == "session" }
        entries = filtered
        startWatching()
        return filtered
    }

    /// Returns the tracker entry matching `type` whose `ts` is within
    /// `tolerance` seconds of `date`, or nil.
    func cost(matching type: String, near date: Date, tolerance: TimeInterval) -> UsageEntry? {
        entries.first { entry in
            entry.type == type && abs(entry.ts.timeIntervalSince(date)) <= tolerance
        }
    }

    private func startWatching() {
        watchTask?.cancel()
        let url = trackerURL
        watchTask = Task { [weak self] in
            guard let self else { return }
            for await _ in self.fileEvents.events(for: url) {
                let refreshed = self.parseFile(url)
                let filtered = refreshed.filter { ($0.source ?? "session") == "session" }
                self.entries = filtered
            }
        }
    }

    nonisolated private func parseFile(_ url: URL) -> [UsageEntry] {
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { return [] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { dec in
            let c = try dec.singleValueContainer()
            let s = try c.decode(String.self)
            let f = ISO8601DateFormatter()
            f.formatOptions = [.withInternetDateTime]
            if let d = f.date(from: s) { return d }
            f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let d = f.date(from: s) { return d }
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "unparseable ts: \(s)")
        }
        var out: [UsageEntry] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            guard let d = line.data(using: .utf8) else { continue }
            if let entry = try? decoder.decode(UsageEntry.self, from: d) {
                out.append(entry)
            }
            // Skip un-parseable lines silently — defensive against historical
            // corruption (fixed Apr-16).
        }
        return out
    }
}
