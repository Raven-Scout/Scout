import Foundation
import Testing
@testable import Scout

/// The view-mode enums are persisted through `@SceneStorage`, so their raw
/// values are a compatibility contract with previously-saved scenes.
@Suite("View mode enums")
struct ViewModeEnumTests {

    @Test("Action Items view modes persist as list/board and default to list")
    func actionItemsViewMode_contract() {
        #expect(ActionItemsViewMode.allCases.map(\.rawValue) == ["list", "board"])
        #expect(ActionItemsViewMode.default == .list)
        #expect(ActionItemsViewMode(rawValue: "board") == .board)
        #expect(ActionItemsViewMode(rawValue: "grid") == nil)
        #expect(ActionItemsViewMode.list.id == "list")
        #expect(ActionItemsViewMode.board.id == "board")
        #expect(ActionItemsViewMode.list.displayName == "List")
        #expect(ActionItemsViewMode.board.displayName == "Board")
    }

    @Test("Schedules view modes persist as table/cards/timeline and default to table")
    func schedulesViewMode_contract() {
        #expect(SchedulesViewMode.allCases.map(\.rawValue) == ["table", "cards", "timeline"])
        #expect(SchedulesViewMode.default == .table)
        #expect(SchedulesViewMode(rawValue: "timeline") == .timeline)
        #expect(SchedulesViewMode(rawValue: "kanban") == nil)
        for mode in SchedulesViewMode.allCases {
            #expect(mode.id == mode.rawValue)
            #expect(!mode.displayName.isEmpty)
            #expect(mode.isAvailable, "\(mode) should be selectable")
        }
        #expect(SchedulesViewMode.table.displayName == "Table")
        #expect(SchedulesViewMode.cards.displayName == "Cards")
        #expect(SchedulesViewMode.timeline.displayName == "Timeline")
    }

    @Test("view modes are hashable so they key scene storage dictionaries")
    func viewModes_areHashable() {
        #expect(Set(ActionItemsViewMode.allCases).count == 2)
        #expect(Set(SchedulesViewMode.allCases).count == 3)
    }
}

@Suite("StaleScheduleError")
struct StaleScheduleErrorTests {

    @Test("the error names the external modification and tells the user to reload")
    func describesTheConflict() {
        let loaded = Date(timeIntervalSince1970: 1_781_600_000)
        let modified = loaded.addingTimeInterval(120)
        let error = StaleScheduleError(loadedAt: loaded, modifiedAt: modified)

        let text = try? #require(error.errorDescription)
        #expect(text?.contains("schedule.yaml") == true)
        #expect(text?.contains("Reload") == true)
        #expect(text?.contains("\(modified)") == true)
        #expect(error.localizedDescription == error.errorDescription)
        #expect(error.loadedAt == loaded)
        #expect(error.modifiedAt == modified)
    }
}

@Suite("ItemStatus display + parsing edges")
struct ItemStatusDisplayTests {

    @Test("every status has a display name, with unknown preserved verbatim")
    func displayName_perCase() {
        #expect(ItemStatus.open.displayName == "Open")
        #expect(ItemStatus.inProgress.displayName == "In Progress")
        #expect(ItemStatus.done.displayName == "Done")
        #expect(ItemStatus.dropped.displayName == "Dropped")
        #expect(ItemStatus.unknown("Blocked").displayName == "Blocked")
    }

    @Test("an unknown status round-trips its raw text into frontmatter")
    func frontmatterValue_unknownRoundTrips() {
        #expect(ItemStatus.unknown("Blocked").frontmatterValue == "Blocked")
        #expect(ItemStatus.parse("Blocked").frontmatterValue == "Blocked")
    }

    @Test("parsing trims whitespace and ignores case")
    func parse_trimsAndLowercases() {
        #expect(ItemStatus.parse("  OPEN  ") == .open)
        #expect(ItemStatus.parse("Done") == .done)
        #expect(ItemStatus.parse("In-Progress") == .inProgress)
        #expect(ItemStatus.parse("inprogress") == .inProgress)
        #expect(ItemStatus.parse("   ") == .open)     // whitespace-only == missing
    }

    @Test("an unknown status keeps its original casing, not the lowercased probe")
    func parse_unknownKeepsOriginalCasing() {
        #expect(ItemStatus.parse("  Waiting On Alex  ") == .unknown("Waiting On Alex"))
    }
}

@Suite("PerFileItem derived values")
struct PerFileItemDerivedTests {

    private func item(
        status: ItemStatus, path: String = "/tmp/2026-06-15-a.md", body: String = ""
    ) -> PerFileItem {
        PerFileItem(
            fileURL: URL(fileURLWithPath: path),
            date: "2026-06-15", title: "A", status: status, priority: .medium,
            source: nil, area: nil, bodyMarkdown: body)
    }

    @Test("identity is the file path")
    func id_isFilePath() {
        #expect(item(status: .open, path: "/tmp/x.md").id == "/tmp/x.md")
    }

    @Test("isActive mirrors the status split")
    func isActive_mirrorsStatus() {
        #expect(item(status: .open).isActive)
        #expect(item(status: .inProgress).isActive)
        #expect(!item(status: .done).isActive)
        #expect(!item(status: .dropped).isActive)
        // #85/#93: an unrecognised status is *active*. Only an explicit
        // done/dropped resolves an item — a typo'd status must not silently
        // disappear from the Awaiting list.
        #expect(item(status: .unknown("Blocked")).isActive)
    }

    @Test("body blocks are derived from the markdown body")
    func bodyBlocks_derivedFromMarkdown() {
        #expect(item(status: .open, body: "").bodyBlocks.isEmpty)
        #expect(!item(status: .open, body: "A paragraph.").bodyBlocks.isEmpty)
    }

    @Test("two items with the same fields are equal")
    func equality_isFieldwise() {
        #expect(item(status: .open) == item(status: .open))
        #expect(item(status: .open) != item(status: .done))
    }
}

@Suite("Proposal derived values")
struct ProposalDerivedTests {

    private func proposal(status: ProposalStatus, date: String = "2026-06-15") -> Proposal {
        Proposal(
            fileURL: URL(fileURLWithPath: "/tmp/\(date)-a.md"),
            date: date, title: "A", status: status, bodyMarkdown: "**Trigger:** the demo.")
    }

    @Test("identity is the file path and the header chip is the date")
    func idAndCode() {
        let p = proposal(status: .proposed)
        #expect(p.id == "/tmp/2026-06-15-a.md")
        #expect(p.code == "2026-06-15")
    }

    @Test("isAwaitingDecision mirrors the status")
    func isAwaitingDecision_mirrorsStatus() {
        #expect(proposal(status: .proposed).isAwaitingDecision)
        #expect(proposal(status: .pending(autoApplyDate: "2026-06-20")).isAwaitingDecision)
        #expect(!proposal(status: .approved).isAwaitingDecision)
        #expect(!proposal(status: .rejected).isAwaitingDecision)
        #expect(!proposal(status: .applied(date: "2026-06-16")).isAwaitingDecision)
        #expect(!proposal(status: .unknown("Weird")).isAwaitingDecision)
    }

    @Test("body blocks are derived from the markdown body")
    func bodyBlocks_derivedFromMarkdown() {
        #expect(!proposal(status: .proposed).bodyBlocks.isEmpty)
    }
}
