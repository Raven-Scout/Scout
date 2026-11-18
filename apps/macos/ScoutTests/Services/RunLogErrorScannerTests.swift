import Testing
import Foundation
@testable import Scout

/// Finding #24: the Errors tab said "No errors detected." for a run that died
/// on a traceback, because the scan only knew usage-limit patterns.
@Suite("Run log error scanner")
struct RunLogErrorScannerTests {
    @Test func aPythonTracebackIsOneErrorHeadedByItsException() {
        let log = """
        === Scout run starting at Mon Nov 16 08:00:00 UTC 2026 ===
        Traceback (most recent call last):
          File "/tmp/engine/scout/cli.py", line 12, in main
            run_slot(key)
          File "/tmp/engine/scout/slots.py", line 40, in run_slot
            raise ValueError("no such slot")
        ValueError: no such slot
        === Scout run finished at Mon Nov 16 08:01:00 UTC 2026 (exit code: 1) ===
        """
        let errors = RunLogErrorScanner.scan(log)
        #expect(errors.count == 1)
        let e = try! #require(errors.first)
        #expect(e.kind == .traceback)
        #expect(e.line == 2)
        #expect(e.snippet == "ValueError: no such slot")
        #expect(e.block.count == 6)
        #expect(e.block.first == "Traceback (most recent call last):")
        #expect(e.block.last == "ValueError: no such slot")
    }

    @Test func aChainedTracebackStaysOneError() {
        let log = """
        Traceback (most recent call last):
          File "a.py", line 1, in <module>
        KeyError: 'slot'

        During handling of the above exception, another exception occurred:

        Traceback (most recent call last):
          File "a.py", line 3, in <module>
        RuntimeError: schedule unreadable
        next line of the log
        """
        let errors = RunLogErrorScanner.scan(log)
        #expect(errors.count == 1)
        #expect(errors[0].snippet == "RuntimeError: schedule unreadable")
        #expect(errors[0].block.count == 9)
        #expect(!errors[0].block.contains("next line of the log"))
    }

    @Test func aTracebackEndsAtTheExceptionLine() {
        let log = """
        Traceback (most recent call last):
          File "a.py", line 1, in <module>
        OSError: disk full
        Session summary: wrote 3 notes
        """
        let errors = RunLogErrorScanner.scan(log)
        #expect(errors.count == 1)
        #expect(errors[0].block.last == "OSError: disk full")
    }

    @Test func aCommandsErrorBoxIsOneError() {
        let log = """
        Usage: scoutctl schedule fire-now [OPTIONS] SLOT_KEY
        ╭─ Error ──────────────────────────────╮
        │ No such option: --bypass-budget      │
        ╰──────────────────────────────────────╯
        """
        let errors = RunLogErrorScanner.scan(log)
        #expect(errors.count == 1)
        #expect(errors[0].kind == .error)
        #expect(errors[0].snippet == "No such option: --bypass-budget")
        #expect(errors[0].block.count == 3)
    }

    @Test func errorLinesTakeTheirIndentedDetail() {
        let log = """
        Error: the tracing job failed
            at step one
            at step two
        done
        """
        let errors = RunLogErrorScanner.scan(log)
        #expect(errors.count == 1)
        #expect(errors[0].kind == .error)
        #expect(errors[0].block.count == 3)
    }

    @Test func oneLineErrorsOfEveryShape() {
        let log = """
        ERROR something broke
        fatal: not a git repository
        scout.errors.ConfigError: bad schedule
        run-scout.sh: line 42: jq: command not found
        frobnicate: command not found
        === model resolve failed — running on opus ===
        """
        let errors = RunLogErrorScanner.scan(log)
        #expect(errors.map(\.line) == [1, 2, 3, 4, 5, 6])
        #expect(errors.last?.kind == .runner)
        #expect(errors.dropLast().allSatisfy { $0.kind == .error })
        #expect(errors.allSatisfy { $0.block.isEmpty })
    }

    /// The old rate-limit and API matches stay, with the old pattern ids the
    /// Control Center's rate-limit signal reads.
    @Test func usageLimitLinesKeepTheirPattern() {
        let errors = RunLogErrorScanner.scan("API Error: 429 Too Many Requests\nall fine\n")
        #expect(errors.count == 1)
        #expect(errors[0].kind == .api)
        #expect(errors[0].pattern == "429")
    }

    /// An API error in an error line's indented detail still feeds the
    /// rate-limit signal (review of #686).
    @Test func anErrorLinesDetailKeepsItsAPIPattern() {
        let errors = RunLogErrorScanner.scan("ERROR request failed\n    status 429\n")
        #expect(errors.count == 1)
        #expect(errors[0].kind == .error)
        #expect(errors[0].pattern == "429")
    }

    /// One huge line can't make "Show all" slow to lay out.
    @Test func aHugeBlockLineIsCut() {
        let huge = "    " + String(repeating: "x", count: 50_000)
        let e = try! #require(RunLogErrorScanner.scan("Error: it broke\n\(huge)\n").first)
        #expect(e.block.count == 2)
        #expect(e.block[1].count == RunLogErrorScanner.maxBlockLineLength + 1)
        #expect(e.block[1].hasSuffix("…"))
    }

    /// Prose that only mentions an error, and the runner's ordinary lines,
    /// aren't errors.
    @Test func ordinaryLinesAreNotErrors() {
        let log = """
        === Scout run starting at Mon Nov 16 08:00:00 UTC 2026 ===
        === model: sonnet ===
        === Budget check: skipping this run ===
        Fixed two errors in the parser and noted the error handling plan.
        - Error budget review is on Friday's list
        """
        #expect(RunLogErrorScanner.scan(log).isEmpty)
    }

    @Test func aLongBlockKeepsItsEnds() {
        let frames = (1...200).map { "  File \"f.py\", line \($0), in g" }
        let log = (["Traceback (most recent call last):"] + frames + ["RecursionError: too deep"]).joined(separator: "\n")
        let e = try! #require(RunLogErrorScanner.scan(log).first)
        #expect(e.block.count == RunLogErrorScanner.maxBlockLines)
        #expect(e.block.first == "Traceback (most recent call last):")
        #expect(e.block.last == "RecursionError: too deep")
        #expect(e.block.contains { $0.hasPrefix("… ") && $0.hasSuffix(" more lines …") })
    }

    /// A cache written by an older build (no kind, no block) still decodes.
    @Test func anOldCachedErrorDecodes() throws {
        let json = #"{"line": 3, "pattern": "429", "snippet": "429 Too Many Requests"}"#
        let e = try JSONDecoder().decode(DetectedError.self, from: Data(json.utf8))
        #expect(e.kind == .api)
        #expect(e.block.isEmpty)
    }
}
