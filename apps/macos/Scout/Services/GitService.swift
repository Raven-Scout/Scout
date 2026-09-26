import Foundation

final class GitService: @unchecked Sendable {
    private let repoURL: URL
    private let runner: any ProcessRunner
    /// How many times to retry an index-locking git op (`add`/`commit`) when
    /// `.git/index.lock` is contended by a concurrent (plugin) git process.
    private let maxLockRetries: Int
    /// Backoff between lock retries, given the 1-based attempt number.
    /// Injectable so tests don't actually sleep.
    private let lockBackoff: @Sendable (Int) async -> Void

    init(
        repoURL: URL,
        runner: any ProcessRunner,
        maxLockRetries: Int = 3,
        lockBackoff: @escaping @Sendable (Int) async -> Void = GitService.defaultLockBackoff
    ) {
        self.repoURL = repoURL
        self.runner = runner
        self.maxLockRetries = maxLockRetries
        self.lockBackoff = lockBackoff
    }

    /// 50 ms × attempt (50, 100, 150 …) — short enough to clear a transient
    /// lock from a sibling git process, bounded by `maxLockRetries`.
    static let defaultLockBackoff: @Sendable (Int) async -> Void = { attempt in
        try? await Task.sleep(nanoseconds: UInt64(attempt) * 50_000_000)
    }

    /// List commits in the given time range whose subject starts with `prefix`.
    /// An empty `prefix` means "no subject filter" — return all commits in
    /// the window. This is what `Run.commits(for:)` falls back to for `.manual`
    /// runs, where we don't know which family the user invoked.
    func commits(
        between start: Date,
        and end: Date,
        matchingPrefix prefix: String
    ) async throws -> [Commit] {
        let iso = ISO8601DateFormatter()
        let sep = "\u{1E}"                  // ASCII record separator
        let format = ["%H", "%h", "%ct", "%s"].joined(separator: sep)
        let args = [
            "-C", repoURL.path,
            "log",
            "--since=\(iso.string(from: start))",
            "--until=\(iso.string(from: end))",
            "--format=\(format)",
            "--shortstat"
        ]
        let result = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["git"] + args,
            environment: [:],
            workingDirectory: repoURL
        )
        guard result.exitCode == 0 else {
            throw GitServiceError.gitExitNonZero(Int(result.exitCode))
        }
        let text = String(data: result.stdout, encoding: .utf8) ?? ""
        return parse(gitLogOutput: text, prefix: prefix)
    }

    /// Return the diff between two commits (or ranges like "A^..B").
    func diff(from shaA: String, to shaB: String) async throws -> String {
        let result = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["git", "-C", repoURL.path, "diff", "\(shaA)..\(shaB)"],
            environment: [:],
            workingDirectory: repoURL
        )
        return String(data: result.stdout, encoding: .utf8) ?? ""
    }

    private func parse(gitLogOutput: String, prefix: String) -> [Commit] {
        var commits: [Commit] = []
        // Each commit is: "<sha>\u{1E}<short>\u{1E}<unix-ts>\u{1E}<subject>\n<shortstat-line>?\n?"
        let lines = gitLogOutput.components(separatedBy: "\n")
        var i = 0
        while i < lines.count {
            let line = lines[i]
            if line.contains("\u{1E}") {
                let parts = line.components(separatedBy: "\u{1E}")
                guard parts.count == 4 else { i += 1; continue }
                let sha = parts[0], short = parts[1], ts = parts[2], subject = parts[3]
                if !prefix.isEmpty && !subject.hasPrefix(prefix) { i += 1; continue }
                let date = Date(timeIntervalSince1970: TimeInterval(ts) ?? 0)
                var filesChanged = 0, insertions = 0, deletions = 0
                if i + 1 < lines.count {
                    let stats = lines[i + 1]
                    if stats.contains("file") {
                        (filesChanged, insertions, deletions) = Self.parseShortStat(stats)
                        i += 1
                    }
                }
                commits.append(Commit(
                    id: sha, shortSHA: short, timestamp: date,
                    subject: subject,
                    filesChanged: filesChanged,
                    insertions: insertions, deletions: deletions
                ))
            }
            i += 1
        }
        return commits
    }

    static func parseShortStat(_ s: String) -> (Int, Int, Int) {
        func captureInt(_ pattern: String, in s: String) -> Int {
            guard let re = try? NSRegularExpression(pattern: pattern) else { return 0 }
            let range = NSRange(s.startIndex..., in: s)
            if let m = re.firstMatch(in: s, range: range),
               let r = Range(m.range(at: 1), in: s) {
                return Int(s[r]) ?? 0
            }
            return 0
        }
        return (
            captureInt(#"(\d+) files? changed"#, in: s),
            captureInt(#"(\d+) insertions?"#, in: s),
            captureInt(#"(\d+) deletions?"#, in: s)
        )
    }
}

enum GitServiceError: Error, Equatable {
    case gitExitNonZero(Int)
    case commitFailed(exitCode: Int32, stderr: String)
    /// `.git/index.lock` stayed contended after `maxLockRetries` retries — a
    /// concurrent git process (plugin session) wouldn't release it (#48).
    case indexLockContended(stderr: String)
}

protocol GitServiceProtocol: Sendable {
    func commitPaths(_ relPaths: [String], message: String) async throws
}

extension GitService {
    /// Stage all changes in the repo and create a commit with ``message``.
    /// No-ops cleanly if the working tree is clean or if ``repoURL`` is not
    /// a git repo. Never throws on either of those conditions — hardening
    /// move #3 must not take down a successful CLI write when git is absent.
    func commitAll(message: String) async throws {
        // Bail silently if not a git repo.
        let checkRepo = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["git", "-C", repoURL.path, "rev-parse", "--is-inside-work-tree"],
            environment: [:],
            workingDirectory: repoURL
        )
        guard checkRepo.exitCode == 0 else { return }

        // Stage all changes (retry through transient index.lock contention).
        let add = try await runGitLockRetried(["-C", repoURL.path, "add", "-A"])
        if add.exitCode != 0 { throw Self.failure(for: add) }

        // If nothing is staged (e.g. clean tree or only ignored files), skip.
        let diffIndex = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["git", "-C", repoURL.path, "diff", "--cached", "--quiet"],
            environment: [:],
            workingDirectory: repoURL
        )
        if diffIndex.exitCode == 0 { return }  // exit 0 means no staged diff

        let commit = try await runGitLockRetried(["-C", repoURL.path, "commit", "-m", message])
        if commit.exitCode != 0 { throw Self.failure(for: commit) }
    }

    /// Run `git <args>` retrying on `.git/index.lock` contention with backoff,
    /// up to `maxLockRetries`. Returns the final `ProcessResult` (which may
    /// still be a failure if the lock never cleared — callers inspect
    /// `exitCode` and surface via ``failure(for:)``).
    private func runGitLockRetried(_ args: [String]) async throws -> ProcessResult {
        var attempt = 0
        while true {
            let result = try await runner.run(
                executable: URL(fileURLWithPath: "/usr/bin/env"),
                arguments: ["git"] + args,
                environment: [:],
                workingDirectory: repoURL
            )
            if result.exitCode == 0
                || attempt >= maxLockRetries
                || !Self.isIndexLockContention(result) {
                return result
            }
            attempt += 1
            await lockBackoff(attempt)
        }
    }

    /// True when a non-zero git result is an `.git/index.lock` collision —
    /// another git process holds the index lock.
    static func isIndexLockContention(_ result: ProcessResult) -> Bool {
        guard result.exitCode != 0 else { return false }
        let stderr = (String(data: result.stderr, encoding: .utf8) ?? "").lowercased()
        return stderr.contains("index.lock") || stderr.contains("another git process")
    }

    /// Map a failed git result to the right error — `.indexLockContended` for a
    /// persistent lock collision, `.commitFailed` otherwise.
    private static func failure(for result: ProcessResult) -> GitServiceError {
        let stderr = String(data: result.stderr, encoding: .utf8) ?? ""
        if isIndexLockContention(result) {
            return .indexLockContended(stderr: stderr)
        }
        return .commitFailed(exitCode: result.exitCode, stderr: stderr)
    }

    /// Commit only the given paths with the given message. Any unrelated
    /// staged work in the repo is left untouched.
    ///
    /// 1. `git rev-parse --is-inside-work-tree` — bail silently if not a repo.
    /// 2. `git add -- <paths>` — stage only the named paths.
    /// 3. `git diff --cached --quiet -- <paths>` — if exit 0, nothing to commit.
    /// 4. `git commit -m <message> -- <paths>` — scoped commit.
    func commitPaths(_ relPaths: [String], message: String) async throws {
        let repo = repoURL.path

        let checkRepo = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
            environment: [:], workingDirectory: repoURL
        )
        guard checkRepo.exitCode == 0 else { return }

        // Stage the named paths, retrying through transient index.lock
        // contention. A persistent add failure is surfaced (was silently
        // ignored, which let the diff check below see "nothing staged" and
        // no-op — leaving the change uncommitted and clobberable, #48).
        let add = try await runGitLockRetried(["-C", repo, "add", "--"] + relPaths)
        if add.exitCode != 0 { throw Self.failure(for: add) }

        let diff = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["git", "-C", repo, "diff", "--cached", "--quiet", "--"] + relPaths,
            environment: [:], workingDirectory: repoURL
        )
        if diff.exitCode == 0 { return }

        let commit = try await runGitLockRetried(["-C", repo, "commit", "-m", message, "--"] + relPaths)
        if commit.exitCode != 0 { throw Self.failure(for: commit) }
    }
}

extension GitService: GitServiceProtocol {}

/// Production `ProcessRunner`. Spawns a real child process.
struct SystemProcessRunner: ProcessRunner {
    func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) async throws -> ProcessResult {
        let p = Process()
        p.executableURL = executable
        p.arguments = arguments
        p.currentDirectoryURL = workingDirectory
        if !environment.isEmpty {
            var env = ProcessInfo.processInfo.environment
            for (k, v) in environment { env[k] = v }
            p.environment = env
        }
        let outPipe = Pipe()
        let errPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = errPipe

        // `run()` launches and returns immediately; it throws synchronously if
        // the executable can't be spawned. Start draining only after a
        // successful launch so the error path never leaks a blocked reader.
        try p.run()

        // Drain both pipes concurrently rather than reading them inside the
        // termination handler. A child that writes more than the ~64KB pipe
        // buffer blocks on write until someone reads; a terminationHandler-only
        // reader never runs (the child can't terminate), so the call deadlocks
        // and silently wedges whatever awaited it — schedule refreshes, the
        // writer's git commits, etc. (issue #22 audit). `readDataToEndOfFile`
        // returns at EOF, which the child reaches once we've drained it.
        async let outData = Self.drain(outPipe.fileHandleForReading)
        async let errData = Self.drain(errPipe.fileHandleForReading)
        let out = await outData
        let err = await errData

        // Both pipes hit EOF → the child has closed its write ends and is
        // essentially done; this reaps it so `terminationStatus` is valid and
        // returns near-instantly.
        p.waitUntilExit()
        return ProcessResult(exitCode: p.terminationStatus, stdout: out, stderr: err)
    }

    /// Read a file handle to EOF on a background thread, off the cooperative
    /// executor, so the blocking read can't starve Swift concurrency.
    private static func drain(_ handle: FileHandle) async -> Data {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                cont.resume(returning: handle.readDataToEndOfFile())
            }
        }
    }
}
