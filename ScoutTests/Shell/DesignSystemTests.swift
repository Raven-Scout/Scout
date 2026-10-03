import SwiftUI
import Testing
@testable import Scout

/// The design system is the single source of truth for every colour and font
/// the app renders, so these tests lock the section-kind mappings and make
/// sure each palette constant actually resolves (the fallback initialisers run
/// even when the asset catalog has no entry).
@MainActor
@Suite("DesignSystem")
struct DesignSystemTests {

    private let allKinds: [ActionSection.Kind] = [
        .urgent, .todo, .watching, .personal, .focus, .meetings, .done, .digest, .neutral,
    ]

    // MARK: - kind → colour

    @Test("each section kind maps to its documented priority hue")
    func priorityColor_perKind() {
        #expect(DS.priorityColor(.urgent) == DS.Priority.urgent)
        #expect(DS.priorityColor(.todo) == DS.Priority.todo)
        #expect(DS.priorityColor(.watching) == DS.Priority.watch)
        #expect(DS.priorityColor(.personal) == DS.Priority.personal)
        #expect(DS.priorityColor(.done) == DS.Priority.done)
        #expect(DS.priorityColor(.focus) == DS.Accent.fill)
        #expect(DS.priorityColor(.meetings) == DS.Accent.fill)
        #expect(DS.priorityColor(.digest) == DS.Ink.p3)
        #expect(DS.priorityColor(.neutral) == DS.Ink.p4)
    }

    @Test("every kind resolves a colour")
    func priorityColor_isTotal() {
        for kind in allKinds {
            _ = DS.priorityColor(kind)      // must not trap
        }
        // focus and meetings deliberately share the accent; the rest are distinct.
        let distinct = Set(allKinds.map { String(describing: DS.priorityColor($0)) })
        #expect(distinct.count == allKinds.count - 1)
    }

    // NOTE: the emoji glyphs this suite used to assert were replaced by the
    // ring+dot `KindMarker` (#78). `DS.kindSymbol` is covered by
    // `DSKindSymbolTests`; nothing to duplicate here.

    // MARK: - typography

    @Test("the three type voices resolve a font at any size")
    func fonts_resolveForEveryVoice() {
        for size in [9.0, 11.0, 13.0, 18.0, 32.0] as [CGFloat] {
            _ = DS.serif(size)
            _ = DS.mono(size)
            _ = DS.sans(size)
        }
    }

    @Test("weight is applied to each voice")
    func fonts_honourWeight() {
        // A weighted font must differ from the regular one at the same size.
        #expect(DS.serif(13, weight: .bold) != DS.serif(13))
        #expect(DS.mono(13, weight: .bold) != DS.mono(13))
        #expect(DS.sans(13, weight: .bold) != DS.sans(13))
    }

    @Test("different sizes yield different fonts")
    func fonts_varyBySize() {
        #expect(DS.serif(11) != DS.serif(13))
        #expect(DS.mono(11) != DS.mono(13))
        #expect(DS.sans(11) != DS.sans(13))
    }

    @Test("the three voices are distinct from one another")
    func fonts_voicesAreDistinct() {
        #expect(DS.mono(13) != DS.sans(13))
        #expect(DS.serif(13) != DS.sans(13))
    }

    // MARK: - palettes

    @Test("paper, ink, and rule tones each resolve and stay distinct")
    func palette_paperInkRule() {
        let paper = [DS.Paper.base, DS.Paper.sunk, DS.Paper.raised]
        #expect(Set(paper.map { String(describing: $0) }).count == 3)

        let ink = [DS.Ink.p1, DS.Ink.p2, DS.Ink.p3, DS.Ink.p4]
        #expect(Set(ink.map { String(describing: $0) }).count == 4)

        #expect(String(describing: DS.Rule.soft) != String(describing: DS.Rule.hard))
    }

    @Test("accent, priority, and status palettes resolve and stay distinct")
    func palette_accentPriorityStatus() {
        let accent = [DS.Accent.fill, DS.Accent.ink, DS.Accent.wash]
        #expect(Set(accent.map { String(describing: $0) }).count == 3)

        let priority = [DS.Priority.urgent, DS.Priority.todo, DS.Priority.watch,
                        DS.Priority.personal, DS.Priority.done]
        #expect(Set(priority.map { String(describing: $0) }).count == 5)

        let status = [DS.Status.ok, DS.Status.warn, DS.Status.err]
        #expect(Set(status.map { String(describing: $0) }).count == 3)
    }

    @Test("the neumorphic shadow recipe resolves three distinct tones")
    func palette_neumorphic() {
        let tones = [DS.Neumorphic.shadow, DS.Neumorphic.shadow2, DS.Neumorphic.highlight]
        #expect(Set(tones.map { String(describing: $0) }).count == 3)
    }

    // MARK: - shared surface values

    @Test("the editorial rule defaults to the soft hairline tone")
    func editorialRule_defaultsToSoftRule() {
        #expect(EditorialRule().color == DS.Rule.soft)
        #expect(EditorialRule(color: DS.Rule.hard).color == DS.Rule.hard)
    }

    @Test("surface modifiers carry their configured geometry")
    func modifiers_carryGeometry() {
        #expect(NeumorphicRaised().cornerRadius == 8)
        #expect(NeumorphicRaised().small == false)
        #expect(NeumorphicRaised(cornerRadius: 12, small: true).cornerRadius == 12)
        #expect(NeumorphicRaised(cornerRadius: 12, small: true).small)

        #expect(NeumorphicPressed().cornerRadius == 8)
        #expect(NeumorphicPressed(cornerRadius: 4).cornerRadius == 4)

        #expect(EditorialCard().padding == 16)
        #expect(EditorialCard().cornerRadius == 8)
        #expect(EditorialCard().neumorphic == false)
        let custom = EditorialCard(padding: 4, cornerRadius: 2, neumorphic: true)
        #expect(custom.padding == 4)
        #expect(custom.cornerRadius == 2)
        #expect(custom.neumorphic)
    }
}
