import SwiftUI
import Testing
@testable import Scout

/// UI audit T4: at Largest text and 1100 pt, "weekdays" wrapped to "weekda / ys",
/// the COOLDOWN header wrapped, and the table scrolled sideways. The columns now
/// widen with the Text size, drop the word beside the day dots when it won't fit,
/// and stack into cards below that.
@Suite("Schedules columns")
@MainActor
struct SchedulesColumnsTests {
    private let scales = TextScale.Size.allCases.map(\.factor)

    /// The table's width at a window width: the page, less the detail pane, which
    /// opens only with a schedule picked (T4 hand check).
    private func tableWidth(window: CGFloat, selected: Bool) -> CGFloat {
        let page = LayoutSize.pageWidth(window: window)
        switch DetailPaneLayout.layout(pageWidth: page, hasSelection: selected, hidesWhenEmpty: true) {
        case .hidden: return page
        case .shown(let width, _): return page - width
        }
    }

    @Test func theFullestLayoutThatFitsNeverScrolls() {
        for scale in scales {
            let columns = SchedulesColumns(scale: scale)
            #expect(columns.minTotal(.full) > columns.minTotal(.dots))
            for width in stride(from: CGFloat(400), through: 1800, by: 25) {
                let mode = columns.mode(forAvailable: width)
                if mode != .stacked {
                    #expect(columns.minTotal(mode) <= width, "scale \(scale) width \(width)")
                    #expect(columns.nameWidth(forAvailable: width, mode: mode) + columns.fixedTotal(mode)
                            + SchedulesColumns.chrome <= width + 0.5)
                }
                if mode == .dots { #expect(width < columns.minTotal(.full)) }
                if mode == .stacked { #expect(width < columns.minTotal(.dots)) }
            }
        }
    }

    @Test func theAuditsWindows() {
        let standard = SchedulesColumns(scale: 1)
        let largest = SchedulesColumns(scale: TextScale.Size.largest.factor)
        // 1100 pt with nothing selected: Standard keeps its columns, Largest stacks rather than scroll.
        let narrow = tableWidth(window: 1100, selected: false)
        #expect(standard.mode(forAvailable: narrow) != .stacked)
        #expect(largest.mode(forAvailable: narrow) == .stacked)
        // 1600 pt beside the detail pane: Standard has every word, Largest keeps the columns.
        let wide = tableWidth(window: 1600, selected: true)
        #expect(standard.mode(forAvailable: wide) == .full)
        #expect(largest.mode(forAvailable: wide) != .stacked)
        // 1280 pt with nothing picked: no empty pane, so Standard keeps every word.
        #expect(standard.mode(forAvailable: tableWidth(window: 1280, selected: false)) == .full)
    }

    @Test func columnsWidenWithTheTextButNeverShrink() {
        let standard = SchedulesColumns(scale: 1), largest = SchedulesColumns(scale: 1.4)
        #expect(standard.type == SchedulesColumns.baseType && standard.cooldown == SchedulesColumns.baseCooldown)
        #expect(largest.type > standard.type && largest.time > standard.time && largest.cooldown > standard.cooldown)
        #expect(largest.dots > standard.dots)   // DayCircleStrip grows its circles too
        #expect(SchedulesColumns(scale: 0.5) == standard)
    }

    // MARK: Measured: every header and cell fits its column at every Text size

    /// `view`'s width at Standard text. Text width grows with its point size, so
    /// times `scale` it is the width at that Text size (fixed padding and dots only
    /// make this an overestimate).
    private func standardWidth<V: View>(_ view: V) -> CGFloat {
        LayoutHarness.size(of: view.fixedSize(), proposing: CGSize(width: 10_000, height: 200)).width
            / TextScale.shared.factor
    }

    private func expectFits<V: View>(_ view: V, _ column: (SchedulesColumns) -> CGFloat, _ what: String) {
        let width = standardWidth(view)
        for scale in scales {
            #expect(width * scale <= column(SchedulesColumns(scale: scale)) + 1, "\(what) at \(scale)×: \(width * scale)")
        }
    }

    @Test func headersFitOnOneLine() {
        expectFits(SchedulesMasterTable.headerCell("TYPE"), \.type, "TYPE")
        expectFits(SchedulesMasterTable.headerCell("TIME"), \.time, "TIME")
        expectFits(SchedulesMasterTable.headerCell("ON MISS"), \.onMiss, "ON MISS")
        expectFits(SchedulesMasterTable.headerCell("COOLDOWN"), \.cooldown, "COOLDOWN")
        expectFits(SchedulesMasterTable.headerCell("DAYS"), { $0.days(.dots) }, "DAYS")
    }

    @Test func cellsFit() {
        for type in SlotType.allCases { expectFits(TypePill(type: type), \.type, "\(type)") }
        for policy in OnMissPolicy.allCases { expectFits(OnMissPill(policy: policy), \.onMiss, "\(policy)") }
        expectFits(Text("23:59").font(DS.font(.headline, .mono, weight: .semibold)), \.time, "time")
        expectFits(HStack(spacing: 4) {
            Text("120m").font(DS.font(.body, .mono))
            Image(systemName: "bolt.fill").font(DS.font(.micro))
        }, \.cooldown, "cooldown")
        for label in ["weekdays", "weekends", "every day", "Mon, Wed, Fri"] {
            expectFits(Text(label).font(DS.font(.caption)), \.daysLabel, label)
        }
    }
}

/// T4 hand check: at Largest text and 1100 pt, TypePill broke "Consolidation"
/// mid-word on every consolidation card, the cards grew, and row tops no longer
/// lined up. Measured at Standard and scaled, as `SchedulesColumnsTests` does,
/// so no test changes the app-wide Text size under the others.
@Suite("Schedule cards at Largest text")
@MainActor
struct ScheduleCardLargestTextTests {
    private let largest = TextScale.Size.largest.factor

    private func ideal<V: View>(_ view: V) -> CGSize {
        LayoutHarness.size(of: view.fixedSize(), proposing: CGSize(width: 10_000, height: 400))
    }

    @Test("a type pill keeps one line however narrow it's offered")
    func typePillNeverBreaksAWord() {
        for type in SlotType.allCases {
            let whole = ideal(TypePill(type: type))
            let squeezed = LayoutHarness.size(of: TypePill(type: type), proposing: CGSize(width: 20, height: 400))
            #expect(squeezed.height <= whole.height + 0.5, "\(type) wrapped: \(squeezed) vs \(whole)")
            #expect(squeezed.width >= whole.width - 0.5, "\(type) was squeezed: \(squeezed) vs \(whole)")
        }
    }

    /// The narrowest card at 1100 pt is the grid's 240 pt minimum (two beside the
    /// 340 pt detail pane, three without it).
    @Test("at Largest and 1100 pt the narrowest card holds the time and every pill, stacked if not side by side")
    func theNarrowestCardHoldsItsTopRow() {
        let page = LayoutSize.pageWidth(window: 1100)
        #expect(page - 340 - 32 >= SchedulesMasterCards.minCardWidth * 2)
        let inner = SchedulesMasterCards.minCardWidth - 2 * SlotCard.horizontalPadding
        for twentyFourHour in [false, true] {
            let time = ideal(SlotCard.time("12:45", twentyFourHour: twentyFourHour)).width / TextScale.shared.factor
            #expect(time * largest <= inner, "time \(time * largest) > \(inner)")
        }
        for type in SlotType.allCases {
            let pill = ideal(TypePill(type: type)).width / TextScale.shared.factor
            #expect(pill * largest <= inner, "\(type) pill \(pill * largest) > \(inner)")
        }
    }

    @Test("cards in a row share their top edge")
    func cardRowsAlignTops() {
        #expect(SchedulesMasterCards.columns.allSatisfy { $0.alignment == .top })
    }
}
