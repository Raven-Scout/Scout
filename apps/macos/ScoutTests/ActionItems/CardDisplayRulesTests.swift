import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("Card display rules")
struct CardDisplayRulesTests {
    static let expandRules: [(ActionSection.Kind, ActionItemsDisplay.Density, Bool)] = [
        (.urgent, .comfortable, true),
        (.urgent, .compact, false),
        (.todo, .comfortable, false),
        (.todo, .compact, false),
    ]

    @Test(arguments: expandRules)
    func urgentStartsExpandedOnlyWhenComfortable(_ kind: ActionSection.Kind, _ density: ActionItemsDisplay.Density, _ expanded: Bool) {
        #expect(TaskCardView.startsExpanded(kind: kind, density: density) == expanded)
    }

    @Test func refsOffKeepsOnlyTheCarriedChip() {
        let chips = [
            TaskChip(glyph: .linear, label: "PROJ-1234"),
            TaskChip(glyph: .carry, label: "carried Jun 2"),
        ]
        var fields = ActionItemsDisplay.Fields()
        #expect(TaskCardView.visibleChips(chips, fields: fields) == chips)
        fields.refs = false
        #expect(TaskCardView.visibleChips(chips, fields: fields) == [TaskChip(glyph: .carry, label: "carried Jun 2")])
    }
}
