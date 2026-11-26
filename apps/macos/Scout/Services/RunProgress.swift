import Foundation

/// Whether the run holding the vault's session lock is making progress: the line
/// `scoutctl progress watch` keeps in `.scout-logs/.scout-session.lock.alive`
/// (engine `scout/run_progress.py`).
///
/// A run's log is no sign of life. `claude -p` writes it only when it exits, and
/// on a vault linked to Agnes the lease keepalive appends to it every five
/// minutes for as long as the runner shell lives, so a hung claude behind a live
/// shell showed as Running for ever (review of #701). The watcher samples the
/// runner's session without Scout's own helpers (keepalive, brokers, itself) and
/// records how long, in awake time, it has gone without progress: CPU burned or
/// a tool started or ended. The engine's lock reaper and `scoutctl health` read
/// the same line against the same rule, so the app and the engine agree on what
/// a stuck run is.
///
/// One line: `1 <runner pid> <last progress, epoch s> <stalled awake s> <fresh for s> <run log>`.
/// The file's modification time is the watcher's last sample; the line is
/// trusted only while that is within `fresh for`, so a dead watcher (or a Mac
/// that just woke) is never read as a stall, and the caller falls back to the
/// log. `ScoutTests/Fixtures/run-progress.alive` is a byte copy of the engine's
/// generated `plugin/engine/tests/fixtures/contract/run-progress.alive`.
struct RunProgress: Equatable, Sendable {
    let runnerPID: Int
    let progressAt: Date
    /// Awake time since the last progress, as of the watcher's last sample.
    let stalled: TimeInterval
    let freshFor: TimeInterval
    let logPath: String

    nonisolated static let fileName = ".scout-session.lock.alive"
    nonisolated static let formatVersion = "1"

    nonisolated static func parse(_ text: String) -> RunProgress? {
        var line = Substring(text)
        if line.hasSuffix("\n") { line = line.dropLast() }
        guard !line.contains("\n") else { return nil }
        let parts = line.split(separator: " ", maxSplits: 5, omittingEmptySubsequences: false)
        guard parts.count == 6, parts[0] == formatVersion, !parts[5].isEmpty else { return nil }
        let numbers = parts[1...4].map { field -> Int? in
            guard !field.isEmpty, field.allSatisfy({ $0.isASCII && $0.isNumber }) else { return nil }
            return Int(field)
        }
        guard let pid = numbers[0], let progressAt = numbers[1], let stalled = numbers[2],
              let fresh = numbers[3], pid > 0, fresh > 0 else { return nil }
        return RunProgress(
            runnerPID: pid,
            progressAt: Date(timeIntervalSince1970: TimeInterval(progressAt)),
            stalled: TimeInterval(stalled),
            freshFor: TimeInterval(fresh),
            logPath: String(parts[5])
        )
    }

    /// The longest any reader trusts a line, whatever it says (`MAX_FRESH_SECS` in the engine):
    /// three samples at the slowest rate the watcher allows (200 s), plus 30 s.
    nonisolated static let maxFreshFor: TimeInterval = 630
    /// How far in the future a line's modification time may be (clock skew) and still count.
    nonisolated static let freshSlack: TimeInterval = 30
    nonisolated static let maxLineBytes = 4096

    /// The line in `logsDirectory`, while its watcher is alive at `now`; nil otherwise.
    ///
    /// A run can write `.scout-logs`, so the file is opened without following a link and
    /// without blocking (a FIFO planted there must not hang the app), only a regular file is
    /// read, and at most `maxLineBytes` of it. A line is trusted for its own "fresh for",
    /// never longer than `maxFreshFor`, and never when its time is in the future: a forged
    /// line goes stale like a real one.
    nonisolated static func readFresh(logsDirectory: URL, now: Date) -> RunProgress? {
        let path = logsDirectory.appendingPathComponent(fileName).path
        let fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else { return nil }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG else { return nil }
        var buffer = [UInt8](repeating: 0, count: maxLineBytes + 1)
        let count = read(fd, &buffer, maxLineBytes + 1)
        guard count > 0, count <= maxLineBytes,   // a longer file is refused, not torn
              let text = String(bytes: buffer[0..<count], encoding: .utf8),
              let progress = parse(text) else { return nil }
        let modified = Date(timeIntervalSince1970: TimeInterval(info.st_mtimespec.tv_sec)
                            + TimeInterval(info.st_mtimespec.tv_nsec) / 1e9)
        let age = now.timeIntervalSince(modified)
        guard age >= -freshSlack, age <= min(progress.freshFor, maxFreshFor) else { return nil }
        return progress
    }

    /// Whether this line is about the run that writes `log`. Compared by file
    /// name: the runner names the log by its vault path, which may reach the
    /// same file through a symlink, and log names are unique per run.
    nonisolated func names(_ log: URL) -> Bool {
        (logPath as NSString).lastPathComponent == log.lastPathComponent
    }

    /// The watcher's stall for the run that writes `log`, when its fresh line is about that run.
    nonisolated func stall(for log: URL) -> TimeInterval? {
        names(log) ? stalled : nil
    }
}
