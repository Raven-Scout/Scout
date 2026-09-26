import CoreGraphics

/// Single source of truth for the Schedules table's column geometry, shared by
/// the header (`SchedulesMasterTable`) and every data row (`SlotTableRow`) so
/// cells line up under their headers (issue #14).
///
/// NAME is the one flexible column: it grows to fill leftover width when the
/// pane is wide and clamps to `minName` when narrow. The five trailing columns
/// are fixed. When the pane is narrower than `minTotal`, the table scrolls
/// horizontally instead of clipping against the window frame (issue #13).
enum SchedulesColumns {
    static let type: CGFloat = 140
    static let time: CGFloat = 70
    static let days: CGFloat = 250
    static let onMiss: CGFloat = 90
    static let cooldown: CGFloat = 90

    static let spacing: CGFloat = 16
    static let hPadding: CGFloat = 16
    static let minName: CGFloat = 160

    /// Width of the five fixed (non-NAME) columns.
    static let fixedTotal = type + time + days + onMiss + cooldown

    /// Six columns ⇒ five inter-column gaps, plus the row's leading/trailing padding.
    static let chrome = spacing * 5 + hPadding * 2

    /// Minimum width the row needs before columns would start to clip.
    static let minTotal = minName + fixedTotal + chrome

    /// NAME column width for a given available pane width: fills the leftover
    /// when wide, never drops below `minName`.
    static func nameWidth(forAvailable width: CGFloat) -> CGFloat {
        max(minName, width - fixedTotal - chrome)
    }
}
