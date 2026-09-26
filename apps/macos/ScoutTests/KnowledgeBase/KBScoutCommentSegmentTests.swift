import Foundation
import Testing
@testable import Scout

@Suite("KBDocSegment — Scout comment markers")
struct KBScoutCommentSegmentTests {

    // MARK: parse classification

    @Test func markerAfterParagraphParsesAsOwnSegment() {
        let src = "The vault uses tags.\n//==<< scope to people? >>==//\nMore prose."
        let segs = KBDocSegment.segments(from: src)
        #expect(segs.map(\.kind) == [.paragraph, .scoutComment, .paragraph])
        #expect(segs[1].lineStart == 1 && segs[1].lineEnd == 1)
        #expect(ScoutMarker.body(of: segs[1].raw) == "scope to people?")
    }

    @Test func markerDoesNotMergeIntoAdjacentParagraph() {
        // No blank line between prose and marker — still isolated.
        let src = "line a\nline b\n//==<< note >>==//"
        let segs = KBDocSegment.segments(from: src)
        #expect(segs.map(\.kind) == [.paragraph, .scoutComment])
        #expect(segs[0].raw == "line a\nline b")
    }

    @Test func markerAfterTableIsNotSwallowedAsARow() {
        let src = "| A | B |\n| - | - |\n| 1 | 2 |\n//==<< check this >>==//"
        let segs = KBDocSegment.segments(from: src)
        #expect(segs.map(\.kind) == [.table, .scoutComment])
    }

    @Test func markerInsideCodeFenceStaysCode() {
        let src = "```\n//==<< not a comment, it's code >>==//\n```"
        let segs = KBDocSegment.segments(from: src)
        #expect(segs.map(\.kind) == [.code])
    }

    // MARK: insertLine

    @Test func insertLineAfterBlockLeavesOtherBytesIntact() {
        let src = "alpha\nbeta\ngamma"
        // Insert after line index 1 ("beta").
        let out = KBDocSegment.insertLine(in: src, afterLineEnd: 1, line: "//==<< x >>==//")
        #expect(out == "alpha\nbeta\n//==<< x >>==//\ngamma")
    }

    @Test func insertLineAtEndAppends() {
        let src = "alpha\nbeta"
        let out = KBDocSegment.insertLine(in: src, afterLineEnd: 1, line: "//==<< x >>==//")
        #expect(out == "alpha\nbeta\n//==<< x >>==//")
    }

    @Test func insertLineClampsNegativeToPrepend() {
        let out = KBDocSegment.insertLine(in: "only", afterLineEnd: -5, line: "M")
        #expect(out == "M\nonly")
    }

    // MARK: removeLines

    @Test func removeLinesDeletesTheMarkerLine() {
        let src = "alpha\n//==<< x >>==//\nbeta"
        let out = KBDocSegment.removeLines(in: src, start: 1, end: 1)
        #expect(out == "alpha\nbeta")
    }

    @Test func removeLinesOutOfRangeIsNoOp() {
        let src = "alpha\nbeta"
        #expect(KBDocSegment.removeLines(in: src, start: 5, end: 9) == src)
    }
}
