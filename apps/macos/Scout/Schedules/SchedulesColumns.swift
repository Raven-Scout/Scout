import CoreGraphics

/// Single source of truth for the Schedules table's column geometry, shared by
/// the header (`SchedulesMasterTable`) and every data row (`SlotTableRow`) so
/// cells line up under their headers (issue #14).
///
/// NAME is the one flexible column: it grows to fill leftover width when the
/// pane is wide and clamps to `minName` when narrow. The five trailing columns
/// hold text, so they widen with Settings ▸ General ▸ Text size (`scale`),
/// as the day dots do (`DayCircleStrip` scales its circles). At Largest text and 1100 pt the fixed
/// widths wrapped "weekdays" to "weekda / ys", wrapped the COOLDOWN header,
/// and scrolled the table sideways (UI audit T4). Now the table adapts
/// (`mode(forAvailable:)`): it drops the word beside the day dots when the
/// words don't fit, and below that it stacks each schedule into a card
/// rather than scroll.
nonisolated struct SchedulesColumns: Equatable, Sendable {
    /// How the table lays out at a given width.
    nonisolated enum Mode: Equatable, Sendable {
        /// Every column, with "weekdays"-style words beside the day dots.
        case full
        /// Every column; the days column is the dots alone (the words are its tooltip).
        case dots
        /// Too narrow for a row of columns: one card per schedule.
        case stacked
    }

    /// The Text size multiplier (`TextScale.factor`); 1 is Standard.
    let scale: CGFloat

    init(scale: CGFloat = 1) { self.scale = max(1, scale) }

    // Design widths at Standard text. Each holds its header ("COOLDOWN" in
    // tracked caption2) and its widest cell (`SchedulesColumnsTests` measures both).
    static let baseType: CGFloat = 140
    static let baseTime: CGFloat = 64
    static let baseDaysLabel: CGFloat = 100
    static let baseOnMiss: CGFloat = 90
    static let baseCooldown: CGFloat = 90

    /// The seven 16 pt day dots, grown with the Text size as `DayCircleStrip` draws them, and their 4 pt gaps.
    var dots: CGFloat { 7 * DS.scaledSize(16, factor: scale) + 6 * 4 }
    /// The gap between the dots and the word beside them.
    static let dotsGap: CGFloat = 10

    static let spacing: CGFloat = 16
    static let hPadding: CGFloat = 16
    static let minName: CGFloat = 160

    var type: CGFloat { (Self.baseType * scale).rounded(.up) }
    var time: CGFloat { (Self.baseTime * scale).rounded(.up) }
    var daysLabel: CGFloat { (Self.baseDaysLabel * scale).rounded(.up) }
    var onMiss: CGFloat { (Self.baseOnMiss * scale).rounded(.up) }
    var cooldown: CGFloat { (Self.baseCooldown * scale).rounded(.up) }

    /// The days column: the dots, plus the word when `mode` is `.full`.
    func days(_ mode: Mode) -> CGFloat {
        mode == .full ? dots + Self.dotsGap + daysLabel : dots
    }

    /// Width of the five fixed (non-NAME) columns.
    func fixedTotal(_ mode: Mode) -> CGFloat { type + time + days(mode) + onMiss + cooldown }

    /// Six columns ⇒ five inter-column gaps, plus the row's leading/trailing padding.
    static let chrome = spacing * 5 + hPadding * 2

    /// Minimum width a row needs in `mode` before columns would start to clip.
    func minTotal(_ mode: Mode) -> CGFloat { Self.minName + fixedTotal(mode) + Self.chrome }

    /// The fullest layout that fits `width`: words beside the dots, then the dots
    /// alone, then cards. Never a sideways scroll.
    func mode(forAvailable width: CGFloat) -> Mode {
        if width >= minTotal(.full) { return .full }
        if width >= minTotal(.dots) { return .dots }
        return .stacked
    }

    /// NAME column width for a given available pane width: fills the leftover
    /// when wide, never drops below `minName`.
    func nameWidth(forAvailable width: CGFloat, mode: Mode) -> CGFloat {
        max(Self.minName, width - fixedTotal(mode) - Self.chrome)
    }
}
