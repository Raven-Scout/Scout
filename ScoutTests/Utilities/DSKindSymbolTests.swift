import Testing
import SwiftUI
@testable import Scout

@Suite("DS.kindSymbol")
struct DSKindSymbolTests {

    @Test("Priority + neutral kinds have no symbol (they render as a dot)")
    @MainActor
    func test_priority_kinds_have_no_symbol() {
        #expect(DS.kindSymbol(.urgent)   == nil)
        #expect(DS.kindSymbol(.todo)     == nil)
        #expect(DS.kindSymbol(.watching) == nil)
        #expect(DS.kindSymbol(.neutral)  == nil)
    }

    @Test("Category kinds map to their SF Symbol name")
    @MainActor
    func test_category_kinds_map_to_symbols() {
        #expect(DS.kindSymbol(.done)     == "checkmark")
        #expect(DS.kindSymbol(.personal) == "house")
        #expect(DS.kindSymbol(.focus)    == "lightbulb")
        #expect(DS.kindSymbol(.meetings) == "calendar")
        #expect(DS.kindSymbol(.digest)   == "list.clipboard")
    }
}
