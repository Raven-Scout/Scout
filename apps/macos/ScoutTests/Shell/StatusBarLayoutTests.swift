import SwiftUI
import Testing
@testable import Scout

/// UI audit T4 hand re-check, item 5: at Largest text the status bar's row was
/// taller than its fixed 26 pt, spilled upward over the page, and the last
/// Schedules card ended under it. Measured at Standard and scaled, as the
/// Schedules layout tests do, so no test changes the app-wide Text size.
@Suite("Status bar height")
@MainActor
struct StatusBarLayoutTests {
    private func height<V: View>(_ view: V) -> CGFloat {
        LayoutHarness.size(of: view, proposing: CGSize(width: 1000, height: 10_000)).height
    }

    /// The bar's cells are caption text, sans and mono.
    private var rowAtStandard: CGFloat {
        height(HStack {
            Text("next run").font(DS.font(.caption))
            Text("Evening dreaming · 8h 56m").font(DS.font(.caption, .mono))
        }) / TextScale.shared.factor
    }

    @Test("26 pt at Standard text, as the page layouts assume")
    func standardIs26() {
        #expect(rowAtStandard + 2 * StatusBarView.verticalPadding <= StatusBarView.minHeight)
        #expect(height(StatusBarView.chrome { Text("Ready").font(DS.font(.caption)) }) == StatusBarView.minHeight)
        #expect(LayoutSize.windowChrome == 52 + StatusBarView.minHeight)
    }

    @Test("at Largest text the bar is as tall as its row, so the page's safe area clears it")
    func growsWithItsRow() {
        let largest = rowAtStandard * TextScale.Size.largest.factor
        // The row no longer fits the old fixed height…
        #expect(largest + 2 * StatusBarView.verticalPadding > StatusBarView.minHeight)
        // …and the strip takes the row's height rather than letting it spill.
        let bar = height(StatusBarView.chrome { Color.clear.frame(width: 10, height: largest) })
        #expect(abs(bar - (largest + 2 * StatusBarView.verticalPadding)) < 0.5, "bar \(bar) for a \(largest) pt row")
    }
}
