import Testing
@testable import Scout

@Suite("Topic board — remembered expansion")
struct BoardExpansionTests {
    @Test func opensAndClosesWithoutTouchingOtherKeys() {
        var raw = ""
        raw = BoardExpansion.setting("STRM", open: true, in: raw)
        raw = BoardExpansion.setting("__hold", open: true, in: raw)
        #expect(BoardExpansion.contains("STRM", in: raw))
        raw = BoardExpansion.setting("STRM", open: false, in: raw)
        #expect(!BoardExpansion.contains("STRM", in: raw))
        #expect(BoardExpansion.contains("__hold", in: raw))
    }

    @Test func reopeningDoesNotDuplicate() {
        let raw = BoardExpansion.setting("STRM", open: true, in: BoardExpansion.setting("STRM", open: true, in: ""))
        #expect(raw == "STRM")
    }

    @Test func closedByDefault() {
        #expect(!BoardExpansion.contains("STRM", in: ""))
    }

    @Test func pruningKeepsOnlyLiveKeys() {
        #expect(BoardExpansion.pruned("STRM\nstep:GONE\nstep:RTAPI\nOLD", keeping: ["STRM", "step:RTAPI"]) == "STRM\nstep:RTAPI")
    }
}
