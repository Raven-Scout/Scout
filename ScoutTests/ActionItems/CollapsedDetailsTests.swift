import Testing
import Foundation
@testable import Scout

/// `<details>` regions are archive markup, not live work.
///
/// The action-items sessions wrap superseded focus lists and parked run-groups
/// in `<details><summary>…</summary>` so Obsidian collapses them. Before this
/// suite the parser had no branch for HTML blocks: archived `- [x]` lines were
/// parsed as live tasks, and every other line inside a region — including the
/// bare `<details>` / `</details>` tag lines — fell through to the paragraph
/// path and landed in `bullets`, which the 💡 Focus renderer draws as a
/// numbered focus row. A real day's file put 203 rows in Today's Focus (7 of
/// them live) and showed 124 parked rows as live Urgent work.
@Suite("Collapsed <details> regions")
struct CollapsedDetailsTests {
    private static let sourceURL = URL(fileURLWithPath: "/tmp/action-items-2026-05-04.md")

    private func parse(_ body: String) throws -> ActionItemsDocument {
        let text = "# Action Items — Monday, May 4, 2026\n\n" + body
        return try ActionItemsParser.parse(
            text: text,
            sourceURL: Self.sourceURL,
            sourceBytes: text.utf8.count
        )
    }

    private func section(_ body: String, kind: ActionSection.Kind) throws -> ActionSection {
        let doc = try parse(body)
        return try #require(doc.sections.first { $0.kind == kind })
    }

    // MARK: - Tag lines never become content

    @Test("A bare </details> line is never emitted as a bullet")
    func closingTagIsNotABullet() throws {
        let focus = try section("""
        ## 💡 Today's Focus

        1. Ship the parser fix.

        <details><summary>Superseded — yesterday's focus</summary>

        1. Old and no longer true.

        </details>
        """, kind: .focus)

        #expect(focus.bullets == ["1. Ship the parser fix."])
        #expect(!focus.bullets.contains { $0.contains("details") })
    }

    @Test("Archived focus lines do not inflate the live focus list")
    func nestedArchiveDoesNotInflateFocus() throws {
        // Mirrors the real file's shape: each run wraps the previous focus in
        // another `<details>`, nesting without bound.
        let focus = try section("""
        ## 💡 Today's Focus

        **Midday consolidation.** _The window's one real change._

        1. Reply to Priya on the migration thread.
        2. Split the eval three ways.

        ⏸️ **Verified negatives:** no meetings, no new repos.

        <details><summary>Superseded — this morning's focus</summary>

        1. Stale item one.
        2. Stale item two.

        <details><summary>Superseded — yesterday's focus</summary>

        1. Older still.

        </details>

        </details>
        """, kind: .focus)

        #expect(focus.bullets.count == 4)
        #expect(focus.bullets[1] == "1. Reply to Priya on the migration thread.")
        #expect(!focus.bullets.contains { $0.contains("Stale") || $0.contains("Older") })
    }

    // MARK: - Tasks

    @Test("Parked tasks are not live tasks")
    func parkedTasksAreNotLive() throws {
        let urgent = try section("""
        ## 🔴 Urgent / Time-Sensitive

        - [ ] Answer Sam on the rollout window

        <details><summary>🗂️ <b>Parked — 2 run-groups.</b> Expand to work them.</summary>

        - [ ] Old parked row one
        - [x] Old parked row two

        </details>

        - [ ] Confirm the freeze date
        """, kind: .urgent)

        #expect(urgent.tasks.map(\.plainSubject) == [
            "Answer Sam on the rollout window",
            "Confirm the freeze date",
        ])
    }

    @Test("Parked tasks are preserved in a collapsed group, not discarded")
    func parkedTasksArePreserved() throws {
        let urgent = try section("""
        ## 🔴 Urgent / Time-Sensitive

        - [ ] Answer Sam on the rollout window

        <details><summary>🗂️ <b>Parked — 2 run-groups.</b> Expand to work them.</summary>

        - [ ] Old parked row one
        - [x] Old parked row two

        </details>
        """, kind: .urgent)

        #expect(urgent.collapsed.count == 1)
        let group = try #require(urgent.collapsed.first)
        #expect(group.summary == "🗂️ Parked — 2 run-groups. Expand to work them.")
        #expect(group.tasks.map(\.plainSubject) == ["Old parked row one", "Old parked row two"])
        #expect(group.tasks.map(\.done) == [false, true])
    }

    @Test("A nested region flattens into its outermost group")
    func nestedRegionsFlatten() throws {
        let focus = try section("""
        ## 💡 Today's Focus

        1. Live item.

        <details><summary>Superseded — this morning</summary>

        1. Stale item.

        <details><summary>Superseded — yesterday</summary>

        1. Older item.

        </details>

        </details>
        """, kind: .focus)

        #expect(focus.collapsed.count == 1)
        let group = try #require(focus.collapsed.first)
        #expect(group.summary == "Superseded — this morning")
        // The inner summary survives as prose so the archive stays readable,
        // but it does not open a second disclosure.
        #expect(group.bullets == ["1. Stale item.", "Superseded — yesterday", "1. Older item."])
    }

    @Test("An archived table does not stack under the live one")
    func archivedTablesStayInTheirGroup() throws {
        // The 📅 Meetings section archives each day's as-run table under a
        // `<details>`; section-level table collection used to append the
        // superseded ones straight below today's.
        let meetings = try section("""
        ## 📅 Today's Meetings

        | Time | Meeting |
        |---|---|
        | 9:00 AM | Cuesta sync |

        <details><summary>Yesterday — the as-run table</summary>

        | Time | Meeting |
        |---|---|
        | 8:00 AM | Product weekly |
        | 3:00 PM | Retro |

        </details>
        """, kind: .meetings)

        #expect(meetings.tables.count == 1)
        #expect(meetings.tables.first?.rows == [["9:00 AM", "Cuesta sync"]])
        #expect(meetings.collapsed.first?.tables.count == 1)
        #expect(meetings.collapsed.first?.tables.first?.rows.count == 2)
    }

    // MARK: - The two traps in the real file

    @Test("An unclosed <details> is force-closed at the next section header")
    func unclosedRegionDoesNotSwallowTheRestOfTheFile() throws {
        // The real file has 26 opens against 24 closes. A plain depth counter
        // would treat everything after the orphan as archived.
        let doc = try parse("""
        ## 💡 Today's Focus

        1. Live focus item.

        <details><summary>Superseded — never closed</summary>

        1. Stale item.

        ## 🔴 Urgent / Time-Sensitive

        - [ ] Answer Sam on the rollout window
        """)

        let urgent = try #require(doc.sections.first { $0.kind == .urgent })
        #expect(urgent.tasks.map(\.plainSubject) == ["Answer Sam on the rollout window"])
        #expect(urgent.collapsed.isEmpty)

        let focus = try #require(doc.sections.first { $0.kind == .focus })
        #expect(focus.bullets == ["1. Live focus item."])
        #expect(focus.collapsed.count == 1)
        #expect(focus.collapsed.first?.bullets == ["1. Stale item."])
    }

    @Test("An inline-code mention of <details> does not open a region")
    func inlineCodeMentionIsNotATag() throws {
        // Real prose in the vault reads "…so the `<details>` block isn't orphaned."
        let urgent = try section("""
        ## 🔴 Urgent / Time-Sensitive

        - [ ] Check that the `<details>` block isn't orphaned
        - [ ] Answer Sam on the rollout window
        """, kind: .urgent)

        #expect(urgent.tasks.count == 2)
        #expect(urgent.collapsed.isEmpty)
    }

    @Test("A one-line region opens and closes on the same line")
    func singleLineRegion() throws {
        let focus = try section("""
        ## 💡 Today's Focus

        1. Live item.

        <details><summary>Old</summary>Stale prose.</details>

        2. Another live item.
        """, kind: .focus)

        #expect(focus.bullets == ["1. Live item.", "2. Another live item."])
        #expect(focus.collapsed.count == 1)
        #expect(focus.collapsed.first?.summary == "Old")
    }

    @Test("A <details> with no summary still parks its content")
    func regionWithoutSummary() throws {
        let focus = try section("""
        ## 💡 Today's Focus

        1. Live item.

        <details>

        1. Stale item.

        </details>
        """, kind: .focus)

        #expect(focus.bullets == ["1. Live item."])
        #expect(focus.collapsed.first?.summary == "")
        #expect(focus.collapsed.first?.bullets == ["1. Stale item."])
    }

    // MARK: - Identity

    @Test("A collapsed task never collides with a live task's id")
    func collapsedTaskIDsAreScoped() throws {
        // Same subject at the same index in both scopes — before the id scope
        // was namespaced these hashed to the same UUID and SwiftUI saw a
        // duplicate row identity.
        let urgent = try section("""
        ## 🔴 Urgent / Time-Sensitive

        - [ ] Answer Sam on the rollout window

        <details><summary>Parked</summary>

        - [ ] Answer Sam on the rollout window

        </details>
        """, kind: .urgent)

        let live = try #require(urgent.tasks.first)
        let parked = try #require(urgent.collapsed.first?.tasks.first)
        #expect(live.id != parked.id)
    }

    @Test("Collapsed groups keep stable ids across identical reparses")
    func collapsedGroupIDsAreStable() throws {
        let body = """
        ## 🔴 Urgent / Time-Sensitive

        - [ ] Answer Sam on the rollout window

        <details><summary>Parked</summary>

        - [ ] Old parked row

        </details>
        """
        let first = try section(body, kind: .urgent)
        let second = try section(body, kind: .urgent)
        #expect(first.collapsed.map(\.id) == second.collapsed.map(\.id))
        #expect(first.collapsed.first?.tasks.map(\.id) == second.collapsed.first?.tasks.map(\.id))
    }
}

/// The 💡 Focus renderer draws its own ordinal next to each bullet while the
/// source lines already carry `1. `, `2. ` — so rows read "1  1. …". Prose
/// lines (the lede, the ⏸️ footer) were numbered as if they were focus items.
@Suite("Focus ordinals")
struct FocusOrdinalTests {
    @Test("An ordered-list line yields its source number and the text after it")
    func splitsOrdinal() {
        #expect(ActionItemsParser.focusOrdinal("1. Ship the fix").number == 1)
        #expect(ActionItemsParser.focusOrdinal("1. Ship the fix").text == "Ship the fix")
        #expect(ActionItemsParser.focusOrdinal("12.  Ship the fix").number == 12)
        #expect(ActionItemsParser.focusOrdinal("3) Ship the fix").number == 3)
    }

    @Test("Prose keeps its text and carries no number")
    func proseHasNoOrdinal() {
        let lede = "**Midday consolidation.** _The window's one real change._"
        #expect(ActionItemsParser.focusOrdinal(lede).number == nil)
        #expect(ActionItemsParser.focusOrdinal(lede).text == lede)

        let footer = "⏸️ **Verified negatives:** no meetings."
        #expect(ActionItemsParser.focusOrdinal(footer).number == nil)
        #expect(ActionItemsParser.focusOrdinal(footer).text == footer)
    }

    @Test("A decimal or a bare number is not an ordinal")
    func nearMissesAreNotOrdinals() {
        #expect(ActionItemsParser.focusOrdinal("1.5x the cost").number == nil)
        #expect(ActionItemsParser.focusOrdinal("2026 was the year").number == nil)
    }
}
