import Combine
import SwiftUI

/// Vertical 24-hour timeline of schedule slots. Central axis with hour ticks,
/// cards alternating left/right, a live "now" marker, and — critically —
/// **gap-collapse zones**: empty windows longer than 2h compress into a small
/// dashed "X – Y · Nh quiet" pill so the user doesn't have to scroll past
/// midnight every time.
///
/// Layout/algorithm ports the handoff bundle's `TimelineView` in schedules.jsx
/// (Scout.html design bundle): linear time segments alternating with fixed-px
/// skip segments, alternating L/R card layout with collision bump-down.
struct SchedulesTimelineView: View {
    let slots: [Slot]
    @Binding var selectedSlotKey: String?

    @State private var nowMin: Int = currentMinute()
    private let tick = Timer.publish(every: 60, on: .main, in: .common).autoconnect()

    // Layout constants — keep in sync with schedules.jsx
    private let pxPerHour: CGFloat   = 50
    private let pad: CGFloat         = 12
    private let cardH: CGFloat       = 60
    private let gapHours: Int        = 2          // collapse any empty window > 2h
    private let gapPx: CGFloat       = 36
    private let bufBefore: CGFloat   = 0.75       // hours of headroom before first slot
    private let bufAfter: CGFloat    = 0.75       // hours of trailing slack after last slot
    private let axisWidth: CGFloat   = 110        // central axis column
    private let cardSlotHeight: CGFloat = 64      // height reserved per card

    var body: some View {
        let sorted = slots.sorted { minutes($0.firesAtLocal) < minutes($1.firesAtLocal) }
        let timeline = computeSegments(events: sorted.map { minutes($0.firesAtLocal) })
        let positioned = layoutCards(sorted: sorted, yFor: timeline.yForMinute)

        return ScrollView {
            ZStack(alignment: .topLeading) {
                // Background paper sheet for the axis column.
                axisSheet(totalH: timeline.totalH)

                // Hour tick labels.
                ForEach(hourTicks(segments: timeline.segments, yFor: timeline.yForMinute), id: \.h) { t in
                    tickRow(t: t, totalH: timeline.totalH)
                }

                // Skip pills inside the axis.
                ForEach(Array(timeline.segments.enumerated()), id: \.offset) { _, seg in
                    if seg.kind == .skip {
                        skipPill(seg: seg)
                    }
                }

                // NOW marker.
                if showNow(timeline: timeline) {
                    nowMarker(y: timeline.yForMinute(nowMin), totalH: timeline.totalH)
                }

                // Cards.
                ForEach(positioned, id: \.slot.key) { p in
                    card(p: p, totalH: timeline.totalH)
                }
            }
            .frame(height: timeline.totalH)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 24)
            .padding(.bottom, 32)
        }
        .onReceive(tick) { _ in nowMin = Self.currentMinute() }
    }

    // MARK: - Pieces

    private func axisSheet(totalH: CGFloat) -> some View {
        GeometryReader { geo in
            let center = geo.size.width / 2
            ZStack {
                RoundedRectangle(cornerRadius: 14)
                    .fill(DS.Paper.raised)
                    .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                    .frame(width: axisWidth)
                    .position(x: center, y: totalH / 2)
                Rectangle()
                    .fill(DS.Rule.soft)
                    .frame(width: 1, height: totalH)
                    .position(x: center, y: totalH / 2)
            }
        }
        .frame(height: totalH)
    }

    private func tickRow(t: HourTick, totalH: CGFloat) -> some View {
        GeometryReader { geo in
            let center = geo.size.width / 2
            ZStack {
                Rectangle()
                    .fill(t.major ? DS.Ink.p4.opacity(0.4) : DS.Rule.soft)
                    .frame(width: axisWidth - 16, height: 0.5)
                    .position(x: center, y: t.y)
                if t.major {
                    Text(formatHour(t.h))
                        .font(DS.mono(10.5, weight: .medium))
                        .foregroundStyle(DS.Ink.p3)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(RoundedRectangle(cornerRadius: 4).fill(DS.Paper.raised))
                        .position(x: center, y: t.y)
                }
            }
        }
        .frame(height: totalH)
        .allowsHitTesting(false)
    }

    private func skipPill(seg: Segment) -> some View {
        let fromH = seg.startMin / 60
        let toH = Int(ceil(Double(seg.endMin) / 60.0))
        let hrs = max(1, (seg.endMin - seg.startMin) / 60)
        return GeometryReader { geo in
            let center = geo.size.width / 2
            VStack(spacing: 0) {
                Text("\(formatHour(fromH)) – \(formatHour(toH)) · \(hrs)h quiet")
                    .font(DS.mono(10, weight: .medium))
                    .foregroundStyle(DS.Ink.p4)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 3)
                    .background(
                        Capsule().fill(DS.Paper.sunk)
                            .overlay(Capsule().strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                    )
            }
            .position(x: center, y: seg.y0 + gapPx / 2)
        }
        .frame(height: gapPx)
        .offset(y: 0)
        .allowsHitTesting(false)
    }

    private func nowMarker(y: CGFloat, totalH: CGFloat) -> some View {
        GeometryReader { geo in
            let center = geo.size.width / 2
            ZStack {
                Rectangle()
                    .fill(DS.Status.err)
                    .frame(width: axisWidth + 16, height: 1.5)
                    .shadow(color: DS.Status.err.opacity(0.4), radius: 4)
                    .position(x: center, y: y)
                Circle()
                    .fill(DS.Status.err)
                    .overlay(Circle().strokeBorder(DS.Status.err.opacity(0.25), lineWidth: 3).frame(width: 16, height: 16))
                    .frame(width: 10, height: 10)
                    .position(x: center, y: y)
                Text("NOW")
                    .font(DS.mono(9, weight: .semibold))
                    .tracking(1.5)
                    .foregroundStyle(DS.Status.err)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(RoundedRectangle(cornerRadius: 4).fill(DS.Paper.raised))
                    .position(x: center, y: y + 14)
            }
        }
        .frame(height: totalH)
        .allowsHitTesting(false)
    }

    private func card(p: PositionedSlot, totalH: CGFloat) -> some View {
        let slot = p.slot
        let isSelected = selectedSlotKey == slot.key
        let ring = DS.SlotType.color(for: slot.type)
        return GeometryReader { geo in
            let center = geo.size.width / 2
            let cardW = max(160, (geo.size.width - axisWidth - 64) / 2)
            let xCenter: CGFloat = p.side == .left
                ? center - axisWidth / 2 - 16 - cardW / 2
                : center + axisWidth / 2 + 16 + cardW / 2
            let connectorStart: CGFloat = p.side == .left
                ? center - axisWidth / 2
                : center + axisWidth / 2

            ZStack {
                // Connector
                Path { path in
                    path.move(to: CGPoint(x: connectorStart, y: p.y))
                    let cardEdgeX: CGFloat = p.side == .left
                        ? xCenter + cardW / 2
                        : xCenter - cardW / 2
                    path.addLine(to: CGPoint(x: cardEdgeX, y: p.y))
                }
                .stroke(DS.Rule.hard.opacity(0.6), lineWidth: 1)

                // Dot at axis
                Circle()
                    .fill(DS.Paper.raised)
                    .overlay(Circle().strokeBorder(ring, lineWidth: 1.5))
                    .frame(width: 7, height: 7)
                    .position(x: connectorStart, y: p.y)

                // Card
                Button {
                    selectedSlotKey = slot.key
                } label: {
                    cardContents(slot: slot, ring: ring)
                        .frame(width: cardW)
                }
                .buttonStyle(.plain)
                .overlay(alignment: .leading) {
                    Rectangle()
                        .fill(ring.opacity(0.85))
                        .frame(width: 3)
                }
                .clipShape(RoundedRectangle(cornerRadius: 9))
                .background(
                    RoundedRectangle(cornerRadius: 9)
                        .fill(isSelected ? DS.Paper.raised : DS.Paper.base)
                        .overlay(
                            RoundedRectangle(cornerRadius: 9)
                                .strokeBorder(isSelected ? ring.opacity(0.7) : DS.Rule.soft, lineWidth: isSelected ? 1.5 : 0.5)
                        )
                        .shadow(color: DS.Neumorphic.shadow.opacity(0.6), radius: 4, x: 2, y: 2)
                        .shadow(color: DS.Neumorphic.highlight, radius: 3, x: -1, y: -1)
                )
                .position(x: xCenter, y: p.y)
            }
        }
        .frame(height: totalH)
    }

    @ViewBuilder
    private func cardContents(slot: Slot, ring: Color) -> some View {
        let t = formatTime12(slot.firesAtLocal)
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(t.hh + ":" + t.mm)
                    .font(DS.mono(13, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Text(t.suffix.uppercased())
                    .font(DS.mono(9))
                    .foregroundStyle(DS.Ink.p4)
                Spacer(minLength: 0)
                Text(slot.type.rawValue.capitalized)
                    .font(DS.sans(9, weight: .medium))
                    .tracking(0.8)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(ring))
            }
            Text(slot.key)
                .font(DS.mono(11, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
                .lineLimit(2)
                .multilineTextAlignment(.leading)
            HStack(spacing: 6) {
                weekdayStrip(weekdays: slot.weekdays, ring: ring)
                Spacer(minLength: 0)
                Text("\(slot.cooldownMinutes)m cool")
                    .font(DS.mono(9))
                    .foregroundStyle(DS.Ink.p4)
            }
        }
        .padding(.horizontal, 11)
        .padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func weekdayStrip(weekdays: [String], ring: Color) -> some View {
        let on = Set(weekdays)
        let names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return HStack(spacing: 2) {
            ForEach(names, id: \.self) { name in
                let isOn = on.contains(name)
                Text(String(name.prefix(1)))
                    .font(DS.sans(8.5, weight: .medium))
                    .foregroundStyle(isOn ? Color.white : DS.Ink.p4)
                    .frame(width: 12, height: 12)
                    .background(
                        Circle().fill(isOn ? ring : DS.Ink.p4.opacity(0.18))
                    )
            }
        }
    }

    // MARK: - Layout helpers

    /// Result of segmenting a 24-hour band into linear "time" stretches and
    /// fixed-px "skip" stretches. `yForMinute` translates a clock-minute into
    /// the vertical coordinate inside this stack.
    private struct Timeline {
        let segments: [Segment]
        let totalH: CGFloat
        let yForMinute: (Int) -> CGFloat
        let isInSkip: (Int) -> Bool
    }

    private enum SegKind { case time, skip }

    private struct Segment {
        var kind: SegKind
        var startMin: Int
        var endMin: Int
        var y0: CGFloat
        var y1: CGFloat
    }

    private struct HourTick { let h: Int; let y: CGFloat; let major: Bool }

    private enum CardSide { case left, right }

    private struct PositionedSlot {
        let slot: Slot
        let side: CardSide
        let y: CGFloat
    }

    private func computeSegments(events: [Int]) -> Timeline {
        guard let first = events.first, let last = events.last else {
            return Timeline(segments: [], totalH: 200, yForMinute: { _ in 0 }, isInSkip: { _ in false })
        }
        let start = max(0, first - Int(bufBefore * 60))
        let end   = min(24 * 60, last + Int(bufAfter * 60))

        var segs: [Segment] = []
        var cursor = start
        var y: CGFloat = pad
        for i in events.indices {
            let e = events[i]
            let gap = e - cursor
            if gap > gapHours * 60 && i > 0 {
                let tail = 30, head = 30
                segs.append(Segment(kind: .time, startMin: cursor, endMin: cursor + tail,
                                    y0: y, y1: y + (CGFloat(tail) / 60) * pxPerHour))
                y += (CGFloat(tail) / 60) * pxPerHour
                segs.append(Segment(kind: .skip, startMin: cursor + tail, endMin: e - head,
                                    y0: y, y1: y + gapPx))
                y += gapPx
                cursor = e - head
            }
        }
        if cursor < end {
            let span = end - cursor
            segs.append(Segment(kind: .time, startMin: cursor, endMin: end,
                                y0: y, y1: y + (CGFloat(span) / 60) * pxPerHour))
            y += (CGFloat(span) / 60) * pxPerHour
        }

        let totalH = y + pad

        let yFor: (Int) -> CGFloat = { min in
            for seg in segs {
                if min >= seg.startMin && min <= seg.endMin {
                    if seg.kind == .skip { return seg.y0 + (segs.first.map { _ in CGFloat(36) } ?? 36) / 2 }
                    let f = CGFloat(min - seg.startMin) / max(1, CGFloat(seg.endMin - seg.startMin))
                    return seg.y0 + f * (seg.y1 - seg.y0)
                }
            }
            if let f = segs.first, min < f.startMin { return f.y0 }
            return segs.last?.y1 ?? 0
        }
        let isSkip: (Int) -> Bool = { min in
            segs.contains { $0.kind == .skip && min >= $0.startMin && min <= $0.endMin }
        }
        return Timeline(segments: segs, totalH: totalH, yForMinute: yFor, isInSkip: isSkip)
    }

    private func layoutCards(sorted: [Slot], yFor: (Int) -> CGFloat) -> [PositionedSlot] {
        var out: [PositionedSlot] = []
        var lastY: [CardSide: CGFloat] = [.left: -9999, .right: -9999]
        var preferred: CardSide = .right
        let minGap: CGFloat = cardH + 6
        for slot in sorted {
            let baseY = yFor(minutes(slot.firesAtLocal))
            let clearL = baseY - (lastY[.left] ?? -9999)
            let clearR = baseY - (lastY[.right] ?? -9999)
            let side: CardSide
            if clearL >= minGap && clearR >= minGap {
                side = preferred
            } else if clearL >= minGap {
                side = .left
            } else if clearR >= minGap {
                side = .right
            } else {
                side = clearL >= clearR ? .left : .right
            }
            let y = max(baseY, (lastY[side] ?? -9999) + minGap)
            lastY[side] = y
            out.append(PositionedSlot(slot: slot, side: side, y: y))
            preferred = side == .right ? .left : .right
        }
        return out
    }

    private func hourTicks(segments: [Segment], yFor: (Int) -> CGFloat) -> [HourTick] {
        var out: [HourTick] = []
        for seg in segments {
            guard seg.kind == .time else { continue }
            let startH = Int(ceil(Double(seg.startMin) / 60.0))
            let endH = Int(floor(Double(seg.endMin) / 60.0))
            if startH > endH { continue }
            for h in startH...endH {
                out.append(HourTick(h: h, y: yFor(h * 60), major: h % 3 == 0))
            }
        }
        return out
    }

    private func showNow(timeline: Timeline) -> Bool {
        guard !timeline.segments.isEmpty else { return false }
        let first = timeline.segments[0].startMin
        let last = timeline.segments[timeline.segments.count - 1].endMin
        return !timeline.isInSkip(nowMin) && nowMin >= first && nowMin <= last
    }

    private static func currentMinute() -> Int {
        let d = Date()
        let cal = Calendar.current
        return cal.component(.hour, from: d) * 60 + cal.component(.minute, from: d)
    }

    private func minutes(_ s: String) -> Int {
        let parts = s.split(separator: ":").map { Int($0) ?? 0 }
        let h = parts.first ?? 0
        let m = parts.count > 1 ? parts[1] : 0
        return h * 60 + m
    }

    private func formatHour(_ h: Int) -> String {
        if h == 0 { return "12a" }
        if h == 12 { return "12p" }
        return h > 12 ? "\(h - 12)p" : "\(h)a"
    }

    private struct Hm { let hh: String; let mm: String; let suffix: String }
    private func formatTime12(_ s: String) -> Hm {
        let parts = s.split(separator: ":").map { Int($0) ?? 0 }
        let h = parts.first ?? 0
        let m = parts.count > 1 ? parts[1] : 0
        let pm = h >= 12
        let h12 = ((h + 11) % 12) + 1
        let suffix = pm ? "pm" : "am"
        return Hm(hh: String(h12), mm: String(format: "%02d", m), suffix: suffix)
    }
}
