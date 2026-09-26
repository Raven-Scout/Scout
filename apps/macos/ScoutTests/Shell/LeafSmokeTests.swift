import AppKit
import SwiftUI
import Testing
@testable import Scout

/// The remaining leaf views — board cards, schedule rows/cards, pills, and the
/// run row. Each is cheap to render and previously had zero execution.
@MainActor
@Suite("View smoke — leaves", .serialized)
struct LeafSmokeTests {

    // MARK: - Control Center rows

    @Test("a run row renders for every status, selected and not")
    func runRowRendersEveryStatus() {
        for status in [RunStatus.scheduled, .running, .success, .failure, .timeout,
                       .orphaned, .skippedBudget, .skippedConcurrency, .rateLimited] {
            for selected in [false, true] {
                ViewHost.render(
                    RunRow(run: Run.make(status: status), isSelected: selected),
                    size: CGSize(width: 420, height: 56))
            }
        }
    }

    @Test("a run row renders a manually-triggered run and one with a cost")
    func runRowRendersManualAndCostedRuns() {
        ViewHost.render(
            RunRow(run: Run.make(source: .manual, cost: 1.25)),
            size: CGSize(width: 420, height: 56))
        ViewHost.render(
            RunRow(run: Run.make(type: .manual, source: .retry)),
            size: CGSize(width: 420, height: 56))
    }

    // MARK: - Action items — preamble and board

    @Test("the preamble card renders collapsed and expanded")
    func preambleCardRenders() {
        for expanded in [false, true] {
            ViewHost.render(
                PreambleCard(
                    headline: "Three things need you today",
                    body: "Priya is waiting on the RFC, the tracing job is still red, "
                        + "and [[projects/the-demo]] needs a **decision** before Friday.",
                    defaultExpanded: expanded),
                size: CGSize(width: 800, height: 200))
        }
    }

    @Test("the preamble card renders with an empty body")
    func preambleCardRendersEmptyBody() {
        ViewHost.render(
            PreambleCard(headline: "Nothing pressing", body: "", defaultExpanded: true),
            size: CGSize(width: 800, height: 120))
    }

    @Test("a board card renders for every kind")
    func boardCardRendersEveryKind() {
        for kind in [ActionSection.Kind.urgent, .todo, .watching, .personal,
                     .focus, .meetings, .done, .digest, .neutral] {
            ViewHost.render(
                BoardCardView(task: SmokeFixtures.task(), kind: kind),
                size: CGSize(width: 280, height: 160))
        }
    }

    @Test("a board card renders a done and a snoozed task")
    func boardCardRendersDoneAndSnoozed() {
        ViewHost.render(
            BoardCardView(task: SmokeFixtures.task(done: true), kind: .done),
            size: CGSize(width: 280, height: 160))
        ViewHost.render(
            BoardCardView(
                task: SmokeFixtures.task(
                    snoozedUntil: SmokeFixtures.day.addingTimeInterval(86_400)),
                kind: .todo),
            size: CGSize(width: 280, height: 160))
    }

    @Test("the board renders populated and empty")
    func boardRenders() {
        let sections = [
            SmokeFixtures.section(kind: .urgent),
            SmokeFixtures.section(kind: .todo),
            SmokeFixtures.section(kind: .watching),
            SmokeFixtures.section(kind: .done),
        ]
        ViewHost.render(BoardView(sections: sections), size: CGSize(width: 1200, height: 700))
        ViewHost.render(BoardView(sections: []), size: CGSize(width: 1200, height: 400))
    }

    // MARK: - Schedules master views

    @Test("the schedules table renders, with and without a draft row")
    func schedulesMasterTableRenders() {
        var selected: String? = "morning-briefing"
        let binding = Binding(get: { selected }, set: { selected = $0 })

        ViewHost.render(
            SchedulesMasterTable(slots: SmokeFixtures.slots, newDraftSlot: nil,
                                 selectedSlotKey: binding),
            size: CGSize(width: 900, height: 500))
        ViewHost.render(
            SchedulesMasterTable(slots: SmokeFixtures.slots,
                                 newDraftSlot: SmokeFixtures.slot(key: "new-slot-1"),
                                 selectedSlotKey: binding),
            size: CGSize(width: 900, height: 500))
        ViewHost.render(
            SchedulesMasterTable(slots: [], newDraftSlot: nil, selectedSlotKey: binding),
            size: CGSize(width: 900, height: 200))
    }

    @Test("the schedules card grid renders")
    func schedulesMasterCardsRenders() {
        var selected: String? = nil
        let binding = Binding(get: { selected }, set: { selected = $0 })
        ViewHost.render(
            SchedulesMasterCards(slots: SmokeFixtures.slots, newDraftSlot: nil,
                                 selectedSlotKey: binding),
            size: CGSize(width: 900, height: 600))
        ViewHost.render(
            SchedulesMasterCards(slots: [],
                                 newDraftSlot: SmokeFixtures.slot(key: "new-slot-1"),
                                 selectedSlotKey: binding),
            size: CGSize(width: 900, height: 300))
    }

    @Test("a slot card renders for every type, selected and not")
    func slotCardRendersEveryType() {
        for type in [SlotType.briefing, .consolidation, .dreaming, .research, .manual] {
            for selected in [false, true] {
                ViewHost.render(
                    SlotCard(slot: SmokeFixtures.slot(type: type), isSelected: selected),
                    size: CGSize(width: 300, height: 180))
            }
        }
    }

    @Test("a slot table row renders for every type")
    func slotTableRowRendersEveryType() {
        for type in [SlotType.briefing, .consolidation, .dreaming, .research, .manual] {
            ViewHost.render(
                SlotTableRow(slot: SmokeFixtures.slot(type: type), isSelected: false,
                             nameWidth: 220),
                size: CGSize(width: 900, height: 40))
        }
        ViewHost.render(
            SlotTableRow(slot: SmokeFixtures.slot(), isSelected: true, nameWidth: 160),
            size: CGSize(width: 700, height: 40))
    }

    // MARK: - Schedule pills

    @Test("the weekday strip renders across weekday sets")
    func dayCircleStripRenders() {
        let sets: [Set<String>] = [
            [],
            ["Mon", "Tue", "Wed", "Thu", "Fri"],
            ["Sat", "Sun"],
            ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        ]
        for days in sets {
            ViewHost.render(
                DayCircleStrip(activeDays: days, typeColor: DS.SlotType.briefing,
                               diameter: 14),
                size: CGSize(width: 200, height: 24))
        }
    }

    @Test("the on-miss pill renders for every policy")
    func onMissPillRendersEveryPolicy() {
        for policy in [OnMissPolicy.fire, .skip] {
            ViewHost.render(OnMissPill(policy: policy), size: CGSize(width: 90, height: 22))
        }
    }

    @Test("the type pill renders for every slot type")
    func typePillRendersEveryType() {
        for type in [SlotType.briefing, .consolidation, .dreaming, .research, .manual] {
            ViewHost.render(TypePill(type: type), size: CGSize(width: 120, height: 22))
        }
    }

    @Test("the per-file status and priority pills render")
    func perFilePillsRender() {
        for status in [ItemStatus.open, .inProgress, .done, .dropped, .unknown("Blocked")] {
            ViewHost.render(ItemStatusPill(status: status), size: CGSize(width: 120, height: 22))
        }
        for priority in [ItemPriority.urgent, .high, .medium, .low] {
            ViewHost.render(
                ItemPriorityPill(priority: priority),
                size: CGSize(width: 120, height: 22))
        }
    }

    @Test("the proposal status pill renders for every status")
    func proposalStatusPillRenders() {
        let statuses: [ProposalStatus] = [
            .proposed, .pending(autoApplyDate: "2026-06-20"), .approved,
            .rejected, .applied(date: "2026-06-16"), .unknown("Weird"),
        ]
        for status in statuses {
            ViewHost.render(
                ProposalStatusPill(status: status), size: CGSize(width: 140, height: 22))
        }
    }

    // MARK: - CardStyle bridge

    @Test("the legacy CardStyle bridge routes to the design system")
    func cardStyleBridgesToDesignSystem() {
        #expect(CardStyle.cornerRadius == 8)
        #expect(CardStyle.chipCornerRadius == 5)
        for kind in [ActionSection.Kind.urgent, .todo, .watching, .personal,
                     .focus, .meetings, .done, .digest, .neutral] {
            #expect(CardStyle.accent(kind) == DS.priorityColor(kind))
        }
    }
}
