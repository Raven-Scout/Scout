import Foundation
import Testing
@testable import Scout

@Suite("RunWindow")
struct RunWindowTests {
    private func rows(_ count: Int) -> [Int] { Array(0..<count) }

    @Test("a fresh window shows one page, the size of the old fixed cap")
    func freshWindowShowsOnePage() {
        let all = rows(RunWindow.pageSize * 3)
        let window = RunWindow()
        #expect(RunWindow.pageSize == 30)
        #expect(Array(window.visible(all)) == Array(all.prefix(30)))
        #expect(window.hiddenCount(in: all) == 60)
        #expect(window.summary(in: all) == "Showing 30 of 90")
    }

    @Test("a list shorter than a page is shown whole, with no footer")
    func shortListShownWhole() {
        let all = rows(4)
        let window = RunWindow()
        #expect(Array(window.visible(all)) == all)
        #expect(window.hiddenCount(in: all) == 0)
        #expect(window.nextPageCount(in: all) == 0)
        #expect(window.summary(in: all) == nil)
    }

    @Test("show more reveals the next page, never more than remain")
    func showMoreRevealsOnePage() {
        let all = rows(RunWindow.pageSize + 7)
        var window = RunWindow()
        #expect(window.nextPageCount(in: all) == 7)
        window.showMore()
        #expect(window.visible(all).count == all.count)
        #expect(window.summary(in: all) == nil)
    }

    @Test("a row past the first page is reachable")
    func rowPastFirstPageIsReachable() {
        // The old list was `prefix(30)` with no control: run 31 could not be opened.
        let all = rows(100)
        var window = RunWindow()
        #expect(!window.visible(all).contains(75))
        window.showMore()
        window.showMore()
        #expect(window.visible(all).contains(75))
    }

    @Test("reset goes back to one page")
    func resetGoesBackToOnePage() {
        let all = rows(100)
        var window = RunWindow()
        window.showMore()
        window.reset()
        #expect(window.visible(all).count == RunWindow.pageSize)
    }
}
