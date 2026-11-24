import Charts
import SwiftUI

/// The engagement trend: stacked weekly bars of the owner's feedback and reliance, a noise line on
/// the trailing axis, and a marker on the time axis for each change that landed in the range.
/// Selecting a marker calls `onSelect` with that change's id.
struct EngagementTrendView: View {
    let days: [EngagementDay]
    let changes: [ChangeRow]
    /// Passed in so the view is deterministic under test.
    let today: Date
    var calendar: Calendar = .current
    let onSelect: (String) -> Void

    @State private var range: HeatmapRange = .month

    static let emptyMessage = "No engagement recorded yet."

    var body: some View {
        let weeks = TrendModel.weeks(days, range: range, today: today, calendar: calendar)
        let markers = TrendModel.markers(changes, range: range, today: today, calendar: calendar)
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("Engagement")
                    .dsFont(.headline, .serif, weight: .medium)
                    .foregroundStyle(DS.Ink.p1)
                Spacer(minLength: 12)
                EditorialSegmentedControl(
                    selection: $range,
                    options: HeatmapRange.allCases.map { (label: $0.segmentLabel, value: $0) },
                    minSegmentWidth: 44,
                    accessibilityLabel: "Time range")
            }
            if weeks.isEmpty {
                Text(Self.emptyMessage)
                    .font(DS.font(.body))
                    .foregroundStyle(DS.Ink.p3)
                    .padding(.vertical, 12)
            } else {
                chart(weeks: weeks, markers: markers)
                legend
            }
        }
        .padding(14)
        .background(
            RoundedRectangle(cornerRadius: 8).fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
    }

    // MARK: - Chart

    private func chart(weeks: [WeekBucket], markers: [ChangeMarker]) -> some View {
        let window = TrendModel.window(range: range, today: today, calendar: calendar)
        let engagementMax = Double(max(1, weeks.map { $0.feedback + $0.reliance }.max() ?? 1))
        let noiseMax = Double(weeks.map(\.noise).max() ?? 0)
        // One y scale: the noise line is drawn scaled to the bars' height, and the trailing axis
        // reads it back in noise counts.
        let noiseScale = noiseMax > 0 ? engagementMax / noiseMax : 1
        return Chart {
            ForEach(weeks) { week in
                BarMark(x: .value("Week", week.start, unit: .weekOfYear), y: .value("Count", week.feedback))
                    .foregroundStyle(by: .value("Kind", "Feedback"))
                BarMark(x: .value("Week", week.start, unit: .weekOfYear), y: .value("Count", week.reliance))
                    .foregroundStyle(by: .value("Kind", "Reliance"))
            }
            ForEach(weeks) { week in
                LineMark(x: .value("Week", week.start, unit: .weekOfYear),
                         y: .value("Noise", Double(week.noise) * noiseScale),
                         series: .value("Series", "Noise"))
                    .foregroundStyle(DS.Accent.ink)
                    .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
            }
        }
        .chartForegroundStyleScale(["Feedback": DS.Ink.p2, "Reliance": DS.Ink.p4])
        .chartLegend(.hidden)
        .chartXScale(domain: window.lowerBound...window.upperBound)
        .chartYScale(domain: 0...engagementMax)
        .chartYAxis {
            AxisMarks(position: .leading)
            AxisMarks(position: .trailing, values: .automatic(desiredCount: 4)) { value in
                AxisValueLabel {
                    if let y = value.as(Double.self) { Text("\(Int((y / noiseScale).rounded()))") }
                }
            }
        }
        .chartOverlay { proxy in
            markerLayer(proxy: proxy, markers: markers)
        }
        .frame(height: 170)
    }

    /// One button per change, sitting on the time axis at the day it landed.
    private func markerLayer(proxy: ChartProxy, markers: [ChangeMarker]) -> some View {
        let rowsByID = Dictionary(changes.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        return GeometryReader { geometry in
            if let plotFrame = proxy.plotFrame {
                let plot = geometry[plotFrame]
                ForEach(markers) { marker in
                    if let x = proxy.position(forX: marker.date) {
                        let label = rowsByID[marker.id].map(TrendModel.markerLabel) ?? "A change"
                        Button { onSelect(marker.id) } label: { markerShape(marker) }
                            .buttonStyle(.plain)
                            .accessibilityLabel(label)
                            .help(label)
                            .position(x: plot.minX + x, y: plot.maxY)
                    }
                }
            }
        }
    }

    private func markerShape(_ marker: ChangeMarker) -> some View {
        let color = Self.color(marker.tone)
        return ZStack {
            Circle().fill(marker.hollow ? DS.Paper.raised : color)
            Circle().strokeBorder(color, lineWidth: 2)
        }
        .frame(width: 10, height: 10)
        .padding(4)
        .contentShape(Rectangle())
    }

    /// The design system's own status tones: helped green, testing amber, hurt and rolled back red,
    /// everything else grey.
    static func color(_ tone: VerdictTone) -> Color {
        switch tone {
        case .helped: return DS.pillColors(.ok).tint.color
        case .testing: return DS.pillColors(.warn).tint.color
        case .bad: return DS.pillColors(.err).tint.color
        case .neutral: return DS.pillColors(.neutral).tint.color
        }
    }

    private var legend: some View {
        Text("Bars: your feedback and reliance each week. Dashed line: noise (right axis). Dots: changes, green helped, amber testing, red hurt, grey no clear effect; hollow means the reason was inferred.")
            .font(DS.font(.caption))
            .foregroundStyle(DS.Ink.p3)
            .fixedSize(horizontal: false, vertical: true)
    }
}
