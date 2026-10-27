import Testing
import Foundation
@testable import Scout

@Suite("/scout-plan markers (estimate, block, actual)")
struct PlanMarksTests {
    /// Parse a minimal one-section document and return its tasks.
    private func parseTasks(_ taskLines: [String]) throws -> [ActionTask] {
        let text = (["# Action Items", "", "## 🟡 To Do", ""] + taskLines).joined(separator: "\n")
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-09-30.md")
        let doc = try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
        return try #require(doc.sections.first { $0.kind == .todo }).tasks
    }

    private func parseTask(_ taskLines: [String]) throws -> ActionTask {
        try #require(try parseTasks(taskLines).first)
    }

    @Test func markersBecomeThePlanNotComments() throws {
        let task = try parseTask([
            "- [ ] [#PROJ1] **Draft the rollout note**",
            "  - estimate: 45m (raw: 30m, kind: deep)",
            "  - block: 2026-09-30 10:00-10:45 (event: abc123)",
            "  - actual: 1h (2026-09-30)",
        ])
        #expect(task.comments.isEmpty)
        #expect(task.details.isEmpty)
        #expect(task.plan.estimateMinutes == 45)
        #expect(task.plan.rawMinutes == 30)
        #expect(task.plan.kind == "deep")
        #expect(task.plan.block == PlannedBlock(date: "2026-09-30", start: "10:00", end: "10:45", eventID: "abc123"))
        #expect(task.plan.block?.minutes == 45)
        #expect(task.plan.actualMinutes == 60)
        #expect(task.plan.actualDate == "2026-09-30")
    }

    @Test func optionalTailsMayBeLeftOut() throws {
        let task = try parseTask([
            "- [ ] [#OPS2] **Review the runbook**",
            "  - estimate: 1h15m (kind: review)",
            "  - block: 2026-09-30 14:00-15:15",
            "  - actual: 30m",
        ])
        #expect(task.plan.estimateMinutes == 75)
        #expect(task.plan.rawMinutes == nil)
        #expect(task.plan.kind == "review")
        #expect(task.plan.block?.eventID == nil)
        #expect(task.plan.actualDate == nil)
    }

    @Test func thePlanSurvivesLaterSubLines() throws {
        // Every later sub-line rebuilds the task; the plan must be carried along.
        let task = try parseTask([
            "- [ ] [#PROJ1] **Draft the rollout note**",
            "  - estimate: 45m (raw: 30m, kind: deep)",
            "  - block: 2026-09-30 10:00-10:45 (event: abc123)",
            "  - snoozed-until: 2026-10-02",
            "  - Refs: [[people/alex]] · PROJ-1234",
            "  - State: doing",
            "  - alex: ping me when ready",
            "  > priya (2026-09-29 10:00): noted",
            "  //==<< inline note >>==//",
            "  - a plain note",
        ])
        #expect(task.plan.estimateMinutes == 45)
        #expect(task.plan.block?.start == "10:00")
        #expect(task.fields.state == .doing)
        #expect(task.comments.map(\.author) == ["alex", "priya", "user"])
    }

    @Test func unparseableValuesAndDuplicatesStayComments() throws {
        // The engine's rule: the first line per key whose value parses.
        let task = try parseTask([
            "- [ ] [#PROJ1] **Draft the rollout note**",
            "  - block: waiting on Priya",
            "  - estimate: 30m",
            "  - estimate: 1h",
            "  - actual: a while",
            "  - block: 2026-09-30 10:00-10:45",
        ])
        #expect(task.plan.estimateMinutes == 30)
        #expect(task.plan.block?.start == "10:00")
        #expect(task.plan.actualMinutes == nil)
        #expect(task.comments.map(\.author) == ["block", "estimate", "actual"])
    }

    @Test func aReversedBlockParsesAsInTheEngine() throws {
        let task = try parseTask([
            "- [ ] [#PROJ1] **Draft the rollout note**",
            "  - block: 2026-09-30 10:45-10:00",
        ])
        #expect(task.plan.block?.minutes == -45)
        #expect(task.comments.isEmpty)
    }

    @Test func markersEndAtABlankLine() throws {
        let task = try parseTask([
            "- [ ] [#PROJ1] **Draft the rollout note**",
            "  - estimate: 15m",
            "",
            "  - actual: 15m",
        ])
        #expect(task.plan.estimateMinutes == 15)
        #expect(task.plan.actualMinutes == nil)
        #expect(task.comments.map(\.author) == ["actual"])
    }

    @Test func aChildTasksMarkerIsTheChilds() throws {
        let tasks = try parseTasks([
            "- [ ] [#PROJ1] **Plan the demo**",
            "  - [ ] [#OPS2] **Book the room**",
            "    - estimate: 30m",
        ])
        #expect(tasks.map(\.plan.estimateMinutes) == [nil, 30])
        #expect(tasks.allSatisfy { $0.comments.isEmpty })
    }

    @Test(arguments: [
        ("15m", 15), ("45m", 45), ("1h", 60), ("1h15m", 75), ("1h 15m", 75), ("2H", 120),
    ])
    func durationsParse(text: String, minutes: Int) {
        #expect(TaskPlan.parseDuration(text) == minutes)
    }

    @Test(arguments: ["", "0m", "15", "1.5h", "soon", "m15"])
    func garbageDurationsDoNotParse(text: String) {
        #expect(TaskPlan.parseDuration(text) == nil)
    }

    @Test func durationsFormatCanonically() {
        #expect(TaskPlan.formatDuration(45) == "45m")
        #expect(TaskPlan.formatDuration(60) == "1h")
        #expect(TaskPlan.formatDuration(75) == "1h15m")
    }

    @Test func unknownKeysAreNotPlanMarkers() {
        #expect(TaskPlan.none.merging(key: "Source", value: "slack") == nil)
    }

    // MARK: - Comment index for an engine that still counts markers

    @Test func legacyIndexCountsTheMarkersAboveTheComment() throws {
        let task = try parseTask([
            "- [ ] [#PROJ1] **Draft the rollout note**",
            "  - alex: first",
            "  - estimate: 45m",
            "  - block: 2026-09-30 10:00-10:45",
            "  - priya: second",
        ])
        #expect(task.plan.markerPositions == [1, 1])
        #expect(task.commentSelector(at: 1) == .index(1))
        #expect(task.commentSelector(at: 2) == .indexAfterPlanMarks(2, legacyIndex: 4))
    }

    private func writer(_ runner: RecordingRunner) -> ActionItemsWriter {
        ActionItemsWriter(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
            actionItemsDirectory: URL(fileURLWithPath: "/tmp/ai"),
            scoutDirectory: URL(fileURLWithPath: "/tmp"),
            runner: runner,
            gitService: nil
        )
    }

    private func manifest(_ features: String) -> ProcessResult {
        ProcessResult(exitCode: 0, stdout: Data(#"{"features": {\#(features)}, "schema_version": 1}"#.utf8), stderr: Data())
    }

    private func indexArgument(_ call: RecordingRunner.Call) -> String? {
        call.arguments.firstIndex(of: "--index").map { call.arguments[$0 + 1] }
    }

    @Test func anEngineThatSkipsMarkersGetsTheAppsIndex() async throws {
        let runner = RecordingRunner()
        runner.scripted = [manifest(#""planning_v1": true, "comments_skip_plan_marks_v1": true"#)]
        let w = writer(runner)
        let op = WriteOp.deleteComment(subject: "X", shortPrefix: "A3F7", selector: .indexAfterPlanMarks(2, legacyIndex: 4))
        _ = try await w.submit(op, displayedDate: Date())
        _ = try await w.submit(op, displayedDate: Date())
        let calls = await runner.calls
        // One probe; a yes is remembered.
        #expect(calls.map { $0.arguments.first } == ["manifest", "action-items", "action-items"])
        #expect(indexArgument(calls[1]) == "2")
        #expect(indexArgument(calls[2]) == "2")
    }

    @Test func anOlderEngineGetsTheIndexWithMarkersCounted() async throws {
        let runner = RecordingRunner()
        runner.scripted = [manifest(#""planning_v1": true"#)]
        _ = try await writer(runner).submit(
            .editComment(subject: "X", shortPrefix: "A3F7", selector: .indexAfterPlanMarks(2, legacyIndex: 4), newText: "later"),
            displayedDate: Date()
        )
        let calls = await runner.calls
        #expect(calls.count == 2)
        #expect(calls[1].arguments.contains("edit-comment"))
        #expect(indexArgument(calls[1]) == "4")
        #expect(calls[1].arguments.last == "later")
    }

    @Test func aCommentWithNoMarkerAboveItSkipsTheProbe() async throws {
        let runner = RecordingRunner()
        _ = try await writer(runner).submit(
            .deleteComment(subject: "X", shortPrefix: "A3F7", selector: .index(1)),
            displayedDate: Date()
        )
        let calls = await runner.calls
        #expect(calls.count == 1)
        #expect(indexArgument(calls[0]) == "1")
    }

    // MARK: - Card

    @Test func chipLabelShowsTheDayOnlyWhenItIsNotToday() throws {
        var plan = TaskPlan()
        plan.block = PlannedBlock(date: "2026-09-30", start: "10:00", end: "10:45", eventID: nil)
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        let sameDay = try #require(f.date(from: "2026-09-30"))
        let dayBefore = try #require(f.date(from: "2026-09-29"))
        #expect(TaskCardView.planChipLabel(plan, today: sameDay) == "10:00-10:45")
        plan.estimateMinutes = 45
        #expect(TaskCardView.planChipLabel(plan, today: sameDay) == "10:00-10:45 · 45m")
        let other = try #require(TaskCardView.planChipLabel(plan, today: dayBefore))
        #expect(other.hasSuffix(" 10:00-10:45 · 45m"))
        #expect(other.count > "10:00-10:45 · 45m".count)
    }

    @Test func chipLabelAddsTheActualAndHandlesAnEstimateAlone() throws {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        let day = try #require(f.date(from: "2026-09-30"))
        var plan = TaskPlan()
        #expect(TaskCardView.planChipLabel(plan, today: day) == nil)
        plan.estimateMinutes = 45
        #expect(TaskCardView.planChipLabel(plan, today: day) == "45m")
        plan.block = PlannedBlock(date: "2026-09-30", start: "10:00", end: "10:45", eventID: nil)
        plan.actualMinutes = 60
        #expect(TaskCardView.planChipLabel(plan, today: day) == "10:00-10:45 · 45m · took 1h")
    }

    @Test func chipHelpSpellsOutThePlan() {
        var plan = TaskPlan()
        plan.estimateMinutes = 45
        plan.rawMinutes = 30
        plan.kind = "deep"
        #expect(TaskCardView.planChipHelp(plan) == "Estimate 45m (raw 30m), deep")
        plan.block = PlannedBlock(date: "2026-09-30", start: "10:00", end: "10:45", eventID: "x")
        plan.actualMinutes = 60
        plan.actualDate = "2026-09-30"
        #expect(TaskCardView.planChipHelp(plan)
                == "Planned 2026-09-30 10:00-10:45, estimate 45m (raw 30m), deep, took 1h on 2026-09-30")
    }
}
