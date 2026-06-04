import SwiftUI

/// Activity heatmap with selectable range (1/3/6/12 months). Cells are
/// tinted by `DS.Status.ok` intensity; failures get a tiny red overlay dot.
/// Every cell carries a hover tooltip showing date + per-status counts +
/// cost, so the user can interrogate any square directly.
///
/// CC-4: previous version hardcoded 12 months and produced a sparse,
/// hard-to-read grid by default. New default is 1 month with a segmented
/// switcher so the user can zoom out only when they want to see the macro
/// view.
struct ActivityHeatmapView: View {
    @EnvironmentObject var state: AppState
    @Binding var dayFilter: Date?

    @SceneStorage("controlCenter.heatmapRange") private var rangeRaw: String = HeatmapRange.month.rawValue

    private var range: HeatmapRange {
        HeatmapRange(rawValue: rangeRaw) ?? .month
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            let cells = buildCells(range: range)
            grid(for: cells, cellSize: range.cellSize, gap: range.gap)
            legend(totalShown: cells.reduce(0) { $0 + $1.successes + $1.failures })
                .padding(.top, 10)
        }
        .editorialCard(padding: 18)
    }

    // MARK: - Header (label + range switcher)

    private var header: some View {
        HStack(alignment: .center) {
            Text("Activity — last \(range.shortLabel)".uppercased())
                .font(DS.sans(11, weight: .medium))
                .tracking(0.06 * 11)
                .foregroundStyle(DS.Ink.p4)
            Spacer()
            rangeSegment
        }
        .padding(.bottom, 14)
    }

    private var rangeSegment: some View {
        HStack(spacing: 2) {
            ForEach(HeatmapRange.allCases) { r in
                Button {
                    rangeRaw = r.rawValue
                } label: {
                    Text(r.segmentLabel)
                        .font(DS.sans(10.5, weight: .medium))
                        .foregroundStyle(rangeRaw == r.rawValue ? DS.Ink.p1 : DS.Ink.p3)
                        .padding(.horizontal, 8)
                        .frame(height: 20)
                        .background {
                            if rangeRaw == r.rawValue {
                                RoundedRectangle(cornerRadius: 5).fill(DS.Paper.raised)
                                    .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                            }
                        }
                }
                .buttonStyle(.plainHit)
            }
        }
        .padding(2)
        .background(
            RoundedRectangle(cornerRadius: 7).fill(DS.Paper.sunk.opacity(0.7))
                .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
    }

    // MARK: - Grid

    /// At the 1-mo range the GitHub-style "weeks × days" grid wastes most
    /// of the card on horizontal whitespace (5 narrow columns of 7 rows).
    /// Flip to a 7-cols-wide calendar layout — one row per week, weekday
    /// columns labelled across the top — so the card fills horizontally
    /// and each day reads as a real date.
    @ViewBuilder
    private func grid(for cells: [HeatmapCell], cellSize: CGFloat, gap: CGFloat) -> some View {
        if range == .month {
            calendarMonth(cells: cells, gap: gap)
        } else {
            githubGrid(cells: cells, cellSize: cellSize, gap: gap)
        }
    }

    /// GitHub-style columns-of-weeks grid used for the multi-month ranges,
    /// where the macro pattern (day-of-week × week) is what matters.
    private func githubGrid(cells: [HeatmapCell], cellSize: CGFloat, gap: CGFloat) -> some View {
        let weeks = stride(from: 0, to: cells.count, by: 7).map {
            Array(cells[$0..<min($0 + 7, cells.count)])
        }
        return HStack(alignment: .top, spacing: gap) {
            ForEach(weeks.indices, id: \.self) { wi in
                VStack(spacing: gap) {
                    ForEach(weeks[wi], id: \.date) { cell in
                        cellView(cell, size: cellSize)
                    }
                }
            }
        }
    }

    /// Calendar-style 7-column layout — rows are weeks (Sun → Sat),
    /// columns are weekdays.
    ///
    /// Earlier version grouped cells by ISO `yearForWeekOfYear` /
    /// `weekOfYear` (Mon–Sun) and then padded leading nils using
    /// `cal.component(.weekday)` (Sun-start). Those two calendars disagree
    /// about which week a Saturday belongs to, so a range starting on
    /// Saturday ended up bundled with the following Sunday in the same row
    /// → an 8-cell row, plus the wrong column for the first day. Rewrite
    /// with a single Sun-start gregorian calendar throughout: walk the
    /// cells in date order, start a new row each Sunday, pad leading nils
    /// from the first day's weekday only.
    private func calendarMonth(cells: [HeatmapCell], gap: CGFloat) -> some View {
        var cal = Calendar(identifier: .gregorian)
        cal.firstWeekday = 1   // Sunday
        let weekdayNames = ["S", "M", "T", "W", "T", "F", "S"]

        let weeks: [[HeatmapCell?]] = {
            var rows: [[HeatmapCell?]] = []
            var current: [HeatmapCell?] = []
            for cell in cells {
                let wd = cal.component(.weekday, from: cell.date)  // 1 = Sunday
                if wd == 1 && !current.isEmpty {
                    while current.count < 7 { current.append(nil) }
                    rows.append(current)
                    current = []
                }
                if current.isEmpty {
                    current = Array(repeating: nil, count: wd - 1)  // leading pad
                }
                current.append(cell)
            }
            if !current.isEmpty {
                while current.count < 7 { current.append(nil) }
                rows.append(current)
            }
            return rows
        }()

        return VStack(alignment: .leading, spacing: gap) {
            HStack(spacing: gap) {
                ForEach(0..<7, id: \.self) { i in
                    Text(weekdayNames[i])
                        .font(DS.mono(9.5, weight: .medium))
                        .foregroundStyle(DS.Ink.p4)
                        .frame(maxWidth: .infinity)
                }
            }
            ForEach(weeks.indices, id: \.self) { wi in
                HStack(spacing: gap) {
                    ForEach(0..<7, id: \.self) { di in
                        if di < weeks[wi].count, let cell = weeks[wi][di] {
                            calendarCell(cell)
                        } else {
                            Color.clear.frame(maxWidth: .infinity).aspectRatio(1, contentMode: .fit)
                        }
                    }
                }
            }
        }
    }

    /// Calendar-month cell: square that stretches to fill the column, with
    /// the day-of-month numeral inside. Same colour ramp as the github grid.
    private func calendarCell(_ cell: HeatmapCell) -> some View {
        let isSelected = dayFilter.map { Calendar.current.isDate($0, inSameDayAs: cell.date) } ?? false
        let total = cell.successes + cell.failures
        let dayNum = Calendar.current.component(.day, from: cell.date)
        return ZStack(alignment: .topTrailing) {
            RoundedRectangle(cornerRadius: 4)
                .fill(color(for: cell))
                .aspectRatio(1, contentMode: .fit)
                .frame(maxWidth: .infinity)
            // Day-of-month numeral, top-left
            Text("\(dayNum)")
                .font(DS.mono(9, weight: .medium))
                .foregroundStyle(total == 0 ? DS.Ink.p4 : (cell.successes >= 5 ? .white : DS.Ink.p1))
                .padding(.top, 3)
                .padding(.leading, 4)
                .frame(maxWidth: .infinity, alignment: .leading)
            if cell.hasFailure {
                Circle()
                    .fill(DS.Status.err)
                    .frame(width: 6, height: 6)
                    .padding(4)
            }
        }
        .overlay {
            if isSelected {
                RoundedRectangle(cornerRadius: 4).strokeBorder(DS.Accent.ink, lineWidth: 1.25)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture {
            dayFilter = isSelected ? nil : cell.date
        }
        .onHover { hovering in
            if total > 0 {
                if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
            }
        }
        .help(tooltip(for: cell))
    }

    private func cellView(_ cell: HeatmapCell, size: CGFloat) -> some View {
        let isSelected = dayFilter.map { Calendar.current.isDate($0, inSameDayAs: cell.date) } ?? false
        let total = cell.successes + cell.failures
        return RoundedRectangle(cornerRadius: max(2, size * 0.2))
            .fill(color(for: cell))
            .frame(width: size, height: size)
            .overlay(alignment: .topTrailing) {
                Circle()
                    .fill(DS.Status.err)
                    .frame(width: size * 0.3, height: size * 0.3)
                    .opacity(cell.hasFailure ? 1 : 0)
                    .offset(x: size * 0.15, y: -size * 0.15)
            }
            .overlay {
                if isSelected {
                    RoundedRectangle(cornerRadius: max(2, size * 0.2))
                        .strokeBorder(DS.Accent.ink, lineWidth: 1.25)
                }
            }
            .contentShape(Rectangle())
            .onTapGesture {
                dayFilter = isSelected ? nil : cell.date
            }
            .onHover { hovering in
                if total > 0 {
                    if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
                }
            }
            .help(tooltip(for: cell))
    }

    private func tooltip(for cell: HeatmapCell) -> String {
        let dayFmt = cell.date.formatted(.dateTime.weekday(.wide).month().day().year())
        let runs = cell.successes + cell.failures
        if runs == 0 { return "\(dayFmt) · no runs" }
        var parts = ["\(runs) run\(runs == 1 ? "" : "s")"]
        if cell.successes > 0 { parts.append("\(cell.successes) ✓") }
        if cell.failures > 0  { parts.append("\(cell.failures) ✗") }
        if cell.cost > 0 {
            let n = NSDecimalNumber(decimal: cell.cost)
            let fmt = NumberFormatter()
            fmt.numberStyle = .currency
            fmt.currencyCode = "USD"
            fmt.maximumFractionDigits = 2
            if let cs = fmt.string(from: n) { parts.append(cs) }
        }
        return "\(dayFmt) · \(parts.joined(separator: " · "))"
    }

    // MARK: - Legend

    private func legend(totalShown: Int) -> some View {
        HStack(spacing: 6) {
            Text("Less")
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p4)
            ForEach(legendSwatches, id: \.0) { _, c in
                RoundedRectangle(cornerRadius: 2).fill(c).frame(width: 10, height: 10)
            }
            Text("More")
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p4)
            Spacer()
            Text("\(totalShown) session\(totalShown == 1 ? "" : "s") · \(range.shortLabel)")
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p3)
        }
    }

    private var legendSwatches: [(String, Color)] {
        [
            ("0", DS.Paper.sunk),
            ("1", DS.Status.ok.opacity(0.22)),
            ("2", DS.Status.ok.opacity(0.45)),
            ("3", DS.Status.ok.opacity(0.70)),
            ("4", DS.Status.ok),
        ]
    }

    private func color(for c: HeatmapCell) -> Color {
        switch c.successes {
        case 0:     return DS.Paper.sunk
        case 1...2: return DS.Status.ok.opacity(0.22)
        case 3...4: return DS.Status.ok.opacity(0.45)
        case 5...6: return DS.Status.ok.opacity(0.70)
        default:    return DS.Status.ok
        }
    }

    // MARK: - Cell building

    private struct HeatmapCell {
        let date: Date
        let successes: Int
        let failures: Int
        let cost: Decimal
        var hasFailure: Bool { failures > 0 }
    }

    private func buildCells(range: HeatmapRange) -> [HeatmapCell] {
        let runs = state.sessionLogService.runs
        let cal = Calendar.current
        let today = cal.startOfDay(for: Date())
        let dayCount = range.days
        let start = cal.date(byAdding: .day, value: -(dayCount - 1), to: today)!
        return (0..<dayCount).map { offset in
            let day = cal.date(byAdding: .day, value: offset, to: start)!
            let dayRuns = runs.filter { cal.isDate($0.startedAt, inSameDayAs: day) }
            let ok = dayRuns.filter { $0.status == .success }.count
            let bad = dayRuns.filter {
                [.failure, .timeout, .rateLimited].contains($0.status)
            }.count
            let cost = dayRuns.compactMap(\.cost).reduce(Decimal(0), +)
            return HeatmapCell(date: day, successes: ok, failures: bad, cost: cost)
        }
    }
}

/// Selectable activity-heatmap window. Days drive grid width; cellSize/gap
/// scale inversely so the chart fits comfortably regardless of the range.
enum HeatmapRange: String, CaseIterable, Identifiable {
    case month, quarter, halfYear, year

    var id: String { rawValue }

    var days: Int {
        switch self {
        case .month:    return 31      // calendar month; layout pads to 5–6 visible weeks
        case .quarter:  return 91      // ~13 weeks
        case .halfYear: return 182     // ~26 weeks
        case .year:     return 365
        }
    }

    var cellSize: CGFloat {
        switch self {
        case .month:    return 22
        case .quarter:  return 14
        case .halfYear: return 11
        case .year:     return 10
        }
    }

    var gap: CGFloat {
        switch self {
        case .month:    return 4
        case .quarter:  return 3
        default:        return 3
        }
    }

    var segmentLabel: String {
        switch self {
        case .month:    return "1 mo"
        case .quarter:  return "3 mo"
        case .halfYear: return "6 mo"
        case .year:     return "12 mo"
        }
    }

    var shortLabel: String { segmentLabel }
}
