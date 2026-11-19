import Foundation
@testable import Scout

/// Pin a fixture run log's modification time. `SessionLogService` reads it as
/// the run's latest sign of life when deciding whether a run is orphaned, so a
/// test that leaves the real write time in place depends on when it runs.
func pinLogModified(_ url: URL, to date: Date) throws {
    try FileManager.default.setAttributes([.modificationDate: date], ofItemAtPath: url.path)
}

extension String {
    /// Append this string to an existing fixture log, the way a runner does.
    func appendLine(to url: URL) throws {
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }
        try handle.seekToEnd()
        try handle.write(contentsOf: Data(utf8))
    }
}

/// A clock a test can move forward between sweeps.
final class SteppableClock: ClockSource, @unchecked Sendable {
    var date: Date
    init(date: Date) { self.date = date }
    func now() -> Date { date }
}
