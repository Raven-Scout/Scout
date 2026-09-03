//
//  ScoutTests.swift
//  ScoutTests
//

import Testing
import Foundation

struct ScoutTests {

    @Test func fixturesAreAccessible() throws {
        let bundle = Bundle(for: FixtureAnchor.self)
        // Try finding individual fixtures — if any work, we're good.
        let trackerURL = bundle.url(forResource: "usage-tracker", withExtension: "jsonl")
        #expect(trackerURL != nil, "usage-tracker.jsonl should be in the test bundle")

        // The Fixtures directory may or may not be preserved as a folder —
        // depends on Xcode's resource handling. We check both.
        let fixturesDir = bundle.url(forResource: "Fixtures", withExtension: nil)
        let hasFolder = fixturesDir.map { FileManager.default.fileExists(atPath: $0.path) } ?? false
        if !hasFolder {
            // Flat resource layout — still acceptable for tests that look up by name
            let logsURL = bundle.url(forResource: "scout-2026-04-19_08-08", withExtension: "log")
            #expect(logsURL != nil, "at minimum individual fixtures should be reachable")
        }
    }
}

final class FixtureAnchor {}

/// Poll `condition` on the main actor until it holds, or fail after `timeout`.
///
/// Used by the FSEvents watch tests. Those assert *liveness* — that a file
/// event eventually reaches the service's debounced reparse — not latency, so
/// the budget is deliberately generous: CI runs the whole suite in parallel on
/// a few cores, and a 3 s budget flaked there while passing locally in ~300 ms.
/// A genuinely broken watch still fails, just after a longer wait.
@MainActor
func waitUntil(
    timeout: TimeInterval = 30,
    pollInterval: Duration = .milliseconds(50),
    _ description: @autoclosure () -> String = "condition never became true",
    sourceLocation: SourceLocation = #_sourceLocation,
    _ condition: @MainActor () -> Bool
) async {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if condition() { return }
        try? await Task.sleep(for: pollInterval)
    }
    #expect(condition(), "\(description()) within \(timeout)s", sourceLocation: sourceLocation)
}
