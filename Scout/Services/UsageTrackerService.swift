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
        // Subscribe synchronously — calling events(for:) inside the task left
        // a window where events emitted before the task ran were dropped.
        let events = fileEvents.events(for: url)
        watchTask = Task { [weak self] in
            guard let self else { return }
            for await _ in events {
                let refreshed = self.parseFile(url)
                let filtered = refreshed.filter { ($0.source ?? "session") == "session" }
                self.entries = filtered
            }
        }
    }

    nonisolated private func parseFile(_ url: URL) -> [UsageEntry] {
        guard let data = try? Data(contentsOf: url) else { return [] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { dec in
            let c = try dec.singleValueContainer()
            let s = try c.decode(String.self)
            if let d = UsageTimestampFormatters.plain.date(from: s) { return d }
            if let d = UsageTimestampFormatters.fractional.date(from: s) { return d }
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "unparseable ts: \(s)")
        }
        var out: [UsageEntry] = []
        // Split the UTF-8 bytes rather than decoding the file to a String
        // first. Decoding up front made a single torn byte — this file is
        // appended by a shell script while runs are in flight — discard every
        // entry, and `String.split(separator:)` walks Characters, paying
        // Unicode grapheme breaking per byte.
        for lineData in data.split(separator: UInt8(ascii: "\n"), omittingEmptySubsequences: true) {
            if let entry = try? decoder.decode(UsageEntry.self, from: lineData) {
                out.append(entry)
            }
            // Skip un-parseable lines silently — defensive against historical
            // corruption (fixed Apr-16).
        }
        return out
    }
}

/// Two formatters built once, not once per decoded timestamp.
/// `ISO8601DateFormatter()` construction goes all the way into ICU
/// (`udat_open`), and a fresh one per `ts` showed up as 1855 samples of pure
/// allocation in a launch stackshot — the same defect #108 fixed in
/// `ConnectorCall`. The old code retried by mutating `formatOptions` in
/// place, which is exactly why it could not simply be hoisted; two
/// separately-configured instances, never mutated after setup, can be shared
/// (`ISO8601DateFormatter` is documented thread-safe once configured).
private enum UsageTimestampFormatters {
    nonisolated(unsafe) static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    nonisolated(unsafe) static let fractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
}
