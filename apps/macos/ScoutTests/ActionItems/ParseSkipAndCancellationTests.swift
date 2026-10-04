import Foundation
import Testing
@testable import Scout

/// Fixture text for the parse-gate suites. Anonymized per CLAUDE.md — synthetic
/// `[#TAG…]` short prefixes, the shared `Priya`/`Alex` stand-ins, neutral
/// `PROJ-` Linear ids.
///
/// `nonisolated` so the parser suite can build a fixture without hopping to the
/// main actor: the whole point of those tests is that parsing is off-actor work.
nonisolated enum ParseGateFixtures {
    /// A day with `taskCount` tasks — large enough that a parse is long enough
    /// to be superseded mid-flight.
    static func markdown(taskCount: Int, marker: String = "A") -> String {
        var s = "# Action Items — synthetic\n\n## 🔴 Urgent\n\n"
        for i in 0 ..< taskCount {
            s += "- [ ] [#TAG\(i % 90)] **\(marker) task \(i) [[people/priya]]** — body for \(i), PROJ-\(i), `code`\n"
            s += "  - detail sub-bullet for task \(i)\n"
        }
        return s
    }

    /// An isolated defaults suite, so a test that changes the byline cannot
    /// leak into `UserDefaults.standard` or into another test.
    static func defaults(_ name: String = #function) -> UserDefaults {
        let suite = "scout.tests.parse-gate.\(name)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        return defaults
    }

    static func tmpDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    static func day(_ y: Int, _ m: Int, _ d: Int) -> Date {
        Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone.current, year: y, month: m, day: d
        ))!
    }
}

/// The redundant second parse of every write, and the parse nobody is waiting
/// for.
///
/// One checkbox click parses the same bytes twice: `handleOp` reparses
/// explicitly so the change shows immediately, and scoutctl's own write trips
/// FSEvents ~250 ms later. #103 moved both off the main actor and stopped the
/// second one republishing, but both still ran to completion. These cover the
/// two follow-ups — skipping the redundant parse outright on a byte digest, and
/// abandoning a parse whose result is already superseded.
@Suite("ActionItemsDocumentService — parse skip + cancellation")
@MainActor
struct ParseSkipAndCancellationTests {

    // MARK: - The digest gate

    @Test("A reparse of unchanged bytes skips the parse")
    func unchangedReparseSkipsTheParse() async throws {
        let dir = try ParseGateFixtures.tmpDir()
        let date = ParseGateFixtures.day(2026, 4, 20)
        try ParseGateFixtures.markdown(taskCount: 30).write(
            to: dir.appendingPathComponent("action-items-2026-04-20.md"),
            atomically: true, encoding: .utf8
        )

        let service = ActionItemsDocumentService(
            directory: dir, fileEvents: NoopFS(), defaults: ParseGateFixtures.defaults()
        )
        await service.load(date: date)

        // This is the FSEvent-driven reparse that follows every write: same
        // URL, same byline, same bytes. It must not parse at all.
        await service.reparseCurrent()

        guard let outcome = try await service.parseTask?.value else {
            Issue.record("no parse task was recorded"); return
        }
        guard case .unchanged = outcome else {
            Issue.record("the redundant parse was not skipped: \(outcome)"); return
        }
    }

    @Test("The gate is content-based: a same-length edit is never missed")
    func sameLengthEditIsNotMissed() async throws {
        // The reason an mtime/size skip was rejected in #103: flipping
        // `- [ ]` to `- [x]` leaves the file exactly as long. A digest has no
        // such blind spot, and this pins that.
        let before = "# T\n\n## 🔴 Urgent\n\n- [ ] **Reply to Priya** — the RFC is due Friday\n"
        let after = "# T\n\n## 🔴 Urgent\n\n- [x] **Reply to Priya** — the RFC is due Friday\n"
        #expect(before.utf8.count == after.utf8.count, "fixture is not a same-length edit")

        let dir = try ParseGateFixtures.tmpDir()
        let date = ParseGateFixtures.day(2026, 4, 20)
        let url = dir.appendingPathComponent("action-items-2026-04-20.md")
        try before.write(to: url, atomically: true, encoding: .utf8)

        let service = ActionItemsDocumentService(
            directory: dir, fileEvents: NoopFS(), defaults: ParseGateFixtures.defaults()
        )
        await service.load(date: date)

        try after.write(to: url, atomically: true, encoding: .utf8)
        await service.reparseCurrent()

        guard case .loaded(let doc) = service.state else {
            Issue.record("expected .loaded, got \(service.state)"); return
        }
        #expect(doc.sections.first?.tasks.first?.done == true,
                "a same-length edit was swallowed by the skip gate")
    }

    @Test("Switching to a day whose file has identical bytes still reparses")
    func daySwitchWithIdenticalBytesStillReparses() async throws {
        // The document's date comes from the filename, not the bytes, so two
        // days can hash the same. A digest-only key would leave the previous
        // day's document — and its dateline — on screen.
        let dir = try ParseGateFixtures.tmpDir()
        let text = ParseGateFixtures.markdown(taskCount: 5)
        try text.write(to: dir.appendingPathComponent("action-items-2026-04-20.md"),
                       atomically: true, encoding: .utf8)
        try text.write(to: dir.appendingPathComponent("action-items-2026-04-21.md"),
                       atomically: true, encoding: .utf8)

        let service = ActionItemsDocumentService(
            directory: dir, fileEvents: NoopFS(), defaults: ParseGateFixtures.defaults()
        )
        await service.load(date: ParseGateFixtures.day(2026, 4, 20))
        await service.load(date: ParseGateFixtures.day(2026, 4, 21))

        guard case .loaded(let doc) = service.state else {
            Issue.record("expected .loaded, got \(service.state)"); return
        }
        #expect(ActionItemsDay.stem(for: doc.date) == "2026-04-21",
                "the day switch was swallowed by the skip gate")
    }

    @Test("A changed byline busts the gate")
    func changedBylineBustsTheGate() async throws {
        // The byline names the author of every `//==<< … >>==//` inline
        // comment. It is a parse input the file's bytes do not capture, so it
        // has to be part of the gate's key.
        let dir = try ParseGateFixtures.tmpDir()
        let date = ParseGateFixtures.day(2026, 4, 20)
        try """
        # T

        ## 🔴 Urgent

        - [ ] **Reply to Priya** — the RFC is due Friday
          //==<< chased this in standup >>==//
        """.write(to: dir.appendingPathComponent("action-items-2026-04-20.md"),
                  atomically: true, encoding: .utf8)

        let defaults = ParseGateFixtures.defaults()
        defaults.set("alex", forKey: "authorName")
        let service = ActionItemsDocumentService(
            directory: dir, fileEvents: NoopFS(), defaults: defaults
        )
        await service.load(date: date)
        guard case .loaded(let first) = service.state,
              first.sections.first?.tasks.first?.comments.first?.author == "alex" else {
            Issue.record("precondition: expected an inline comment bylined `alex`, got \(service.state)")
            return
        }

        defaults.set("priya", forKey: "authorName")
        await service.reparseCurrent()

        guard case .loaded(let second) = service.state else {
            Issue.record("expected .loaded, got \(service.state)"); return
        }
        #expect(second.sections.first?.tasks.first?.comments.first?.author == "priya",
                "a byline change was swallowed by the skip gate")
    }

    // MARK: - Cancellation

    @Test("A superseded parse is cancelled, not run to completion")
    func supersededParseIsCancelled() async throws {
        let dir = try ParseGateFixtures.tmpDir()
        try ParseGateFixtures.markdown(taskCount: 4000, marker: "SLOW").write(
            to: dir.appendingPathComponent("action-items-2026-04-20.md"),
            atomically: true, encoding: .utf8
        )
        try ParseGateFixtures.markdown(taskCount: 1, marker: "FAST").write(
            to: dir.appendingPathComponent("action-items-2026-04-21.md"),
            atomically: true, encoding: .utf8
        )

        let service = ActionItemsDocumentService(
            directory: dir, fileEvents: NoopFS(), defaults: ParseGateFixtures.defaults()
        )

        async let slow: Void = service.load(date: ParseGateFixtures.day(2026, 4, 20))
        // Long enough for the slow parse to be in flight, short enough that it
        // cannot have finished: 4000 tasks is hundreds of milliseconds.
        try await Task.sleep(nanoseconds: 20_000_000)
        let superseded = service.parseTask
        #expect(superseded != nil, "precondition: no parse was in flight to supersede")

        await service.load(date: ParseGateFixtures.day(2026, 4, 21))
        _ = await slow

        #expect(superseded?.isCancelled == true,
                "the superseded parse was left to run to completion")
    }
}

/// The parser's own half of the cancellation contract: `Task.cancel()` on the
/// detached parse only helps if the parse looks.
@Suite("ActionItemsParser — cancellation")
struct ParserCancellationTests {

    @Test("A parse whose task is cancelled throws instead of running to completion")
    func cancelledParseThrows() async {
        let text = ParseGateFixtures.markdown(taskCount: 4000)
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-04-20.md")

        let task = Task.detached { () throws -> ActionItemsDocument in
            // Begin the parse only once cancellation is already set, so the
            // parser's first check fires and the test never races it.
            while !Task.isCancelled { await Task.yield() }
            return try ActionItemsParser.parse(
                text: text, sourceURL: url, sourceBytes: text.utf8.count
            )
        }
        task.cancel()

        await #expect(throws: CancellationError.self) { try await task.value }
    }

    @Test("An uncancelled parse still returns the whole document")
    func uncancelledParseCompletes() async throws {
        // The cancellation check must not truncate an ordinary parse.
        let text = ParseGateFixtures.markdown(taskCount: 600)
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-04-20.md")

        let doc = try await Task.detached {
            try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
        }.value

        #expect(doc.sections.first?.tasks.count == 600)
    }
}
