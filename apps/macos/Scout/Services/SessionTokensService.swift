import Foundation
import Combine
import SwiftUI

/// Reads `.scout-logs/session-tokens.jsonl` (produced by the Stop hook via
/// `~/Scout/scripts/sum-session-tokens.sh`) and exposes it as a published
/// list of entries plus range-filtered `TokenTotals`.
///
/// Mirrors `UsageTrackerService` in lifecycle and parser tolerance —
/// corrupt lines are silently skipped.
@MainActor
final class SessionTokensService: ObservableObject {
    @Published private(set) var entries: [SessionTokenEntry] = []

    private let trackerURL: URL
    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?

    init(trackerURL: URL, fileEvents: any FileSystemEventSource) {
        self.trackerURL = trackerURL
        self.fileEvents = fileEvents
    }

    func loadInitial() async throws -> [SessionTokenEntry] {
        let parsed = Self.parseFile(trackerURL)
        entries = parsed
        startWatching()
        return parsed
    }

    /// Returns aggregated totals for entries whose `ts` falls within the
    /// half-open interval `[range.lowerBound, range.upperBound)`.
    func totals(in range: Range<Date>) -> TokenTotals {
        TokenTotals(entries: entries.filter { range.contains($0.ts) })
    }

    private func startWatching() {
        watchTask?.cancel()
        let url = trackerURL
        watchTask = Task { [weak self] in
            guard let self else { return }
            for await _ in self.fileEvents.events(for: url) {
                let refreshed = Self.parseFile(url)
                self.entries = refreshed
            }
        }
    }

    nonisolated private static func parseFile(_ url: URL) -> [SessionTokenEntry] {
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { return [] }
        let decoder = SessionTokenEntry.makeDecoder()
        var out: [SessionTokenEntry] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            guard let d = line.data(using: .utf8) else { continue }
            if let entry = try? decoder.decode(SessionTokenEntry.self, from: d) {
                out.append(entry)
            }
            // Corrupt lines silently skipped — matches UsageTrackerService.
        }
        return out
    }
}
