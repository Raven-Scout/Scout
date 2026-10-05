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
        // Subscribe synchronously — calling events(for:) inside the task left
        // a window where events emitted before the task ran were dropped.
        let events = fileEvents.events(for: url)
        watchTask = Task { [weak self] in
            guard let self else { return }
            for await _ in events {
                let refreshed = Self.parseFile(url)
                self.entries = refreshed
            }
        }
    }

    nonisolated private static func parseFile(_ url: URL) -> [SessionTokenEntry] {
        guard let data = try? Data(contentsOf: url) else { return [] }
        let decoder = SessionTokenEntry.makeDecoder()
        var out: [SessionTokenEntry] = []
        // Split the UTF-8 bytes rather than decoding the file to a String
        // first. Decoding up front made a single torn byte — this file is
        // appended by a Stop hook while other sessions run — discard every
        // entry, and `String.split(separator:)` walks Characters, paying
        // Unicode grapheme breaking per byte.
        for lineData in data.split(separator: UInt8(ascii: "\n"), omittingEmptySubsequences: true) {
            if let entry = try? decoder.decode(SessionTokenEntry.self, from: lineData) {
                out.append(entry)
            }
            // Corrupt lines silently skipped — matches UsageTrackerService.
        }
        return out
    }
}
