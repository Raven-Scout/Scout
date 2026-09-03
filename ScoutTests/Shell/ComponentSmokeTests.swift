import AppKit
import SwiftUI
import Testing
@testable import Scout

/// Fixture models for the component smoke tests. Anonymized per CLAUDE.md —
/// Alex / Priya / Sam, `PROJ-1234`, `example-org`, `acme-co.slack.com`.
@MainActor
enum SmokeFixtures {

    static let day = Date(timeIntervalSince1970: 1_781_600_000)

    static func task(
        done: Bool = false,
        subject: String = "[#MIRO] Reply to Priya",
        body: String = "She needs the RFC by Friday — **blocked** on `scoutctl`.",
        comments: [TaskComment] = [],
        deepLinks: [TaskDeepLink] = [],
        snoozedUntil: Date? = nil,
        carriedInFrom: Date? = nil
    ) -> ActionTask {
        ActionTask(
            id: UUID(), lineNumber: 4, done: done,
            subject: subject, plainSubject: subject, body: body,
            comments: comments, deepLinks: deepLinks,
            snoozedUntil: snoozedUntil, carriedInFrom: carriedInFrom)
    }

    static let comments: [TaskComment] = [
        TaskComment(author: "alex", timestamp: "2026-06-15 10:00 AM ET",
                    text: "Saw three alerts in ten minutes."),
        TaskComment(author: "priya", timestamp: "",
                    text: "Probably the queue drain we shipped — see [[projects/the-demo]]."),
    ]

    static let deepLinks: [TaskDeepLink] = [
        .linear(id: "PROJ-1234"),
        .githubPR(repo: "example-org/app", number: 42,
                  rawURL: URL(string: "https://github.com/example-org/app/pull/42")!),
    ]

    static func section(
        kind: ActionSection.Kind = .urgent,
        tasks: [ActionTask]? = nil,
        bullets: [String] = [],
        tables: [ActionSection.Table] = [],
        collapsed: [ActionSection.CollapsedGroup] = []
    ) -> ActionSection {
        ActionSection(
            id: UUID(), emoji: "", title: kind.rawValue.capitalized,
            kind: kind,
            tasks: tasks ?? [task(), task(done: true, subject: "[#RSM] Land PROJ-1234")],
            bullets: bullets, tables: tables, subheads: [], collapsed: collapsed)
    }

    /// A `<details>` archive region — history the section renders behind a
    /// disclosure, never merged into live tasks/bullets.
    static func collapsedGroup() -> ActionSection.CollapsedGroup {
        ActionSection.CollapsedGroup(
            id: UUID(), summary: "Expand to work them",
            tasks: [task(subject: "[#5864M] An archived row")],
            bullets: ["An archived bullet."], tables: [])
    }

    static let meetingsTable = ActionSection.Table(
        headers: ["Time", "Who", "What"],
        rows: [["09:00", "Alex", "Standup"], ["14:00", "Priya", "RFC review"]])

    static func slot(
        key: String = "morning-briefing",
        type: SlotType = .briefing,
        firesAtLocal: String = "08:00"
    ) -> Slot {
        Slot(key: key, type: type, runner: "run-scout.sh", firesAtLocal: firesAtLocal,
             weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"], missedWindowHours: 4,
             onMiss: .fire, cooldownMinutes: 60, budgetUsd: 2.5,
             tz: "America/New_York", runtime: .local)
    }

    static let slots: [Slot] = [
        slot(key: "morning-briefing", type: .briefing, firesAtLocal: "08:00"),
        slot(key: "midday-consolidation", type: .consolidation, firesAtLocal: "12:30"),
        slot(key: "research", type: .research, firesAtLocal: "14:00"),
        slot(key: "dreaming-nightly", type: .dreaming, firesAtLocal: "22:15"),
        slot(key: "adhoc", type: .manual, firesAtLocal: "17:00"),
    ]

    static let commits: [Commit] = [
        Commit(id: "a1b2c3d4e5", shortSHA: "a1b2c3d", timestamp: day,
               subject: "briefing: refresh action items", filesChanged: 3,
               insertions: 42, deletions: 7),
        Commit(id: "f6g7h8i9j0", shortSHA: "f6g7h8i", timestamp: day,
               subject: "briefing: update knowledge base", filesChanged: 1,
               insertions: 5, deletions: 0),
    ]

    static let errors: [DetectedError] = [
        DetectedError(line: 12, pattern: "ERROR", snippet: "ERROR: the tracing job timed out"),
        DetectedError(line: 88, pattern: "Traceback", snippet: "Traceback (most recent call last):"),
    ]

    static func perFileItem(
        status: ItemStatus = .open, priority: ItemPriority = .high
    ) -> PerFileItem {
        PerFileItem(
            fileURL: URL(fileURLWithPath: "/tmp/2026-06-15-batch-the-digest.md"),
            date: "2026-06-15", title: "Batch the digest",
            status: status, priority: priority,
            source: "briefing", area: "observability",
            bodyMarkdown: "Group digest lines by source.\n\n```\nscoutctl digest --batch\n```")
    }

    static func proposal(status: ProposalStatus = .proposed) -> Proposal {
        Proposal(
            fileURL: URL(fileURLWithPath: "/tmp/2026-06-15-tighten-cadence.md"),
            date: "2026-06-15", title: "Tighten the consolidation cadence",
            status: status,
            bodyMarkdown: "**Trigger:** the demo kept timing out.\n\n```\ncooldown_minutes: 30\n```")
    }

    static func kbNode(name: String = "alex.md") -> KBNode {
        KBNode(kind: .file,
               url: URL(fileURLWithPath: "/tmp/knowledge-base/people/\(name)"),
               relativePath: "knowledge-base/people/\(name)",
               name: name, ext: "md", children: [])
    }
}

/// Renders the leaf views that live inside lazy containers — the ones a
/// whole-tab render never materialises. Each is driven with a populated
/// fixture and, where the view branches on state, once per branch.
@MainActor
@Suite("View smoke — components", .serialized)
struct ComponentSmokeTests {

    private let cardSize = CGSize(width: 900, height: 400)

    // MARK: - Action item rows

    @Test("a task card renders across its states")
    func taskCardRendersEveryState() {
        let variants: [ActionTask] = [
            SmokeFixtures.task(),
            SmokeFixtures.task(done: true),
            SmokeFixtures.task(comments: SmokeFixtures.comments),
            SmokeFixtures.task(deepLinks: SmokeFixtures.deepLinks),
            SmokeFixtures.task(snoozedUntil: SmokeFixtures.day.addingTimeInterval(86_400)),
            SmokeFixtures.task(carriedInFrom: SmokeFixtures.day.addingTimeInterval(-86_400)),
            SmokeFixtures.task(body: ""),
        ]
        for task in variants {
            for kind in [ActionSection.Kind.urgent, .todo, .watching, .personal, .done] {
                ViewHost.render(
                    TaskCardView(
                        task: task, kind: kind, displayedDate: SmokeFixtures.day,
                        scoutDirectory: URL(fileURLWithPath: "/tmp/scout"),
                        onOp: { _, _ in }),
                    size: cardSize)
            }
        }
    }

    @Test("a section renders for every kind")
    func sectionRendersEveryKind() {
        for kind in [ActionSection.Kind.urgent, .todo, .watching, .personal,
                     .focus, .meetings, .done, .digest, .neutral] {
            ViewHost.render(
                SectionView(
                    section: SmokeFixtures.section(kind: kind),
                    displayedDate: SmokeFixtures.day,
                    scoutDirectory: URL(fileURLWithPath: "/tmp/scout"),
                    selection: nil,
                    onOp: { _, _ in }),
                size: cardSize)
        }
    }

    @Test("a section renders bullets and tables")
    func sectionRendersBulletsAndTables() {
        ViewHost.render(
            SectionView(
                section: SmokeFixtures.section(
                    kind: .digest, tasks: [],
                    bullets: ["A digest line with a [link](https://example.com).",
                              "Another **bold** line."]),
                displayedDate: SmokeFixtures.day,
                scoutDirectory: URL(fileURLWithPath: "/tmp/scout"),
                selection: nil,
                onOp: { _, _ in }),
            size: cardSize)

        ViewHost.render(
            SectionView(
                section: SmokeFixtures.section(
                    kind: .meetings, tasks: [], tables: [SmokeFixtures.meetingsTable]),
                displayedDate: SmokeFixtures.day,
                scoutDirectory: URL(fileURLWithPath: "/tmp/scout"),
                selection: nil,
                onOp: { _, _ in }),
            size: cardSize)
    }

    @Test("task actions render")
    func taskActionsRender() {
        for kind in [ActionSection.Kind.urgent, .done] {
            ViewHost.render(
                TaskActionsView(
                    task: SmokeFixtures.task(), kind: kind,
                    displayedDate: SmokeFixtures.day,
                    scoutDirectory: URL(fileURLWithPath: "/tmp/scout"),
                    onOp: { _ in }),
                size: CGSize(width: 700, height: 120))
        }
    }

    @Test("the comment list renders with and without handlers")
    func commentListRenders() {
        ViewHost.render(
            CommentListView(comments: SmokeFixtures.comments),
            size: CGSize(width: 700, height: 300))
        ViewHost.render(
            CommentListView(comments: SmokeFixtures.comments,
                            onEdit: { _, _ in }, onDelete: { _ in }),
            size: CGSize(width: 700, height: 300))
        ViewHost.render(CommentListView(comments: []), size: CGSize(width: 700, height: 80))
    }

    @Test("the snooze popover renders")
    func snoozePopoverRenders() {
        ViewHost.render(
            SnoozePopoverView(sourceDate: SmokeFixtures.day, onCommit: { _ in }, onCancel: {}),
            size: CGSize(width: 320, height: 420))
    }

    // MARK: - Schedules

    @Test("the schedules timeline renders a full day of slots")
    func schedulesTimelineRenders() {
        var selected: String? = "morning-briefing"
        let binding = Binding(get: { selected }, set: { selected = $0 })
        ViewHost.render(
            SchedulesTimelineView(slots: SmokeFixtures.slots, selectedSlotKey: binding),
            size: CGSize(width: 900, height: 900))
    }

    @Test("the schedules timeline renders with no slots and with one slot")
    func schedulesTimelineRendersDegenerateCases() {
        var selected: String? = nil
        let binding = Binding(get: { selected }, set: { selected = $0 })
        ViewHost.render(
            SchedulesTimelineView(slots: [], selectedSlotKey: binding),
            size: CGSize(width: 900, height: 500))
        ViewHost.render(
            SchedulesTimelineView(slots: [SmokeFixtures.slot()], selectedSlotKey: binding),
            size: CGSize(width: 900, height: 500))
    }

    @Test("the slot edit form renders for an existing slot and a new draft")
    func slotEditFormRenders() {
        for isNew in [false, true] {
            ViewHost.render(
                SlotEditForm(
                    liveSlot: SmokeFixtures.slot(), isNewDraft: isNew,
                    onSave: { _ in }, onDelete: {}, onFireNow: { _ in },
                    onRevertNewDraft: isNew ? {} : nil),
                size: CGSize(width: 520, height: 900))
        }
    }

    @Test("the slot edit form renders for every slot type")
    func slotEditFormRendersEverySlotType() {
        for type in [SlotType.briefing, .consolidation, .dreaming, .research, .manual] {
            ViewHost.render(
                SlotEditForm(
                    liveSlot: SmokeFixtures.slot(type: type), isNewDraft: false,
                    onSave: { _ in }, onDelete: {}, onFireNow: { _ in },
                    onRevertNewDraft: nil),
                size: CGSize(width: 520, height: 900))
        }
    }

    // MARK: - Proposals & per-file cards

    @Test("a proposal card renders for every status")
    func proposalCardRendersEveryStatus() {
        let statuses: [ProposalStatus] = [
            .proposed, .pending(autoApplyDate: "2026-06-20"), .approved,
            .rejected, .applied(date: "2026-06-16"), .unknown("Weird"),
        ]
        for status in statuses {
            ViewHost.render(
                ProposalCardView(proposal: SmokeFixtures.proposal(status: status),
                                 onDecide: { _ in }),
                size: cardSize)
        }
    }

    @Test("a per-file card renders across statuses and priorities")
    func perFileCardRenders() {
        for status in [ItemStatus.open, .inProgress, .done, .dropped, .unknown("Blocked")] {
            for priority in [ItemPriority.urgent, .high, .medium, .low] {
                ViewHost.render(
                    PerFileItemCardView(
                        item: SmokeFixtures.perFileItem(status: status, priority: priority),
                        optionalLabel: "Source",
                        priorityOptions: [.urgent, .high, .medium, .low],
                        onResolve: { _ in }),
                    size: cardSize)
            }
        }
    }

    @Test("the add-item sheet renders for both tabs")
    func addItemSheetRenders() {
        for config in [PerFileTabConfig.wishlist, .research] {
            ViewHost.render(
                AddItemSheet(config: config, onSubmit: { _, _, _, _ in }, onCancel: {}),
                size: CGSize(width: 560, height: 620))
        }
    }
}
