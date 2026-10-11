import Testing
@testable import Scout

@Suite("Topic board — legend filter storage")
struct BoardFilterStorageTests {
    @Test func togglesStatesInLegendOrder() {
        var raw = ""
        raw = BoardFilterStorage.toggling(.blocked, in: raw)
        raw = BoardFilterStorage.toggling(.mine, in: raw)
        #expect(raw == "mine,blocked")
        raw = BoardFilterStorage.toggling(.blocked, in: raw)
        #expect(raw == "mine")
    }

    @Test func decodeDropsUnknownAndUnfilterableStates() {
        #expect(BoardFilterStorage.decode("mine,hold,done,soon,,waiting") == [.mine, .waiting])
    }

    @Test func emptyMeansNoFilter() {
        #expect(BoardFilterStorage.decode("").isEmpty)
    }
}
