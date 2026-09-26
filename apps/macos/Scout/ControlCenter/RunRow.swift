import SwiftUI

/// Editorial session row: status icon → name/when → status → commits → cost
/// → reveal chevron. No boxes; hairline rules do the separation. CC-8:
/// added selection highlight + hover chevron + pointing cursor so the
/// row is obviously interactive (previous version gave no signal that
/// tapping would do anything).
struct RunRow: View {
    let run: Run
    var isSelected: Bool = false

    @State private var hovering: Bool = false

    var body: some View {
        HStack(alignment: .center, spacing: 0) {
            Image(systemName: iconName)
                .font(.system(size: 12))
                .foregroundStyle(iconColor)
                .frame(width: 20, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(run.displayName)
                        .font(DS.sans(13, weight: .medium))
                        .foregroundStyle(DS.Ink.p1)
                    if run.wasManuallyTriggered && run.type != .manual {
                        manualBadge
                    }
                }
                Text(run.startedAt.formatted(date: .abbreviated, time: .shortened))
                    .font(DS.mono(11.5))
                    .foregroundStyle(DS.Ink.p4)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            statusCell
                .frame(width: 100, alignment: .leading)
            Text(commitsString)
                .font(DS.mono(11.5, weight: .medium))
                .foregroundStyle(DS.Ink.p3)
                .frame(width: 90, alignment: .trailing)
            Text(costString)
                .font(DS.mono(11.5))
                .foregroundStyle(DS.Ink.p3)
                .frame(width: 80, alignment: .trailing)
            Image(systemName: "chevron.right")
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(hovering || isSelected ? DS.Ink.p2 : DS.Ink.p4.opacity(0.5))
                .frame(width: 16, alignment: .trailing)
        }
        .padding(.vertical, 10)
        .padding(.horizontal, 8)
        .background {
            if isSelected {
                RoundedRectangle(cornerRadius: 6).fill(DS.Accent.wash.opacity(0.55))
            } else if hovering {
                RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk.opacity(0.6))
            }
        }
        .overlay(alignment: .bottom) { EditorialRule() }
        .contentShape(Rectangle())
        .onHover { h in
            hovering = h
            if h { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
        .help("Open run detail")
    }

    private var manualBadge: some View {
        Text("manual")
            .font(DS.mono(9.5, weight: .medium))
            .tracking(0.06 * 9.5)
            .foregroundStyle(DS.Ink.p3)
            .padding(.horizontal, 5)
            .padding(.vertical, 1.5)
            .background(EditorialChipBackground())
    }

    private var statusCell: some View {
        HStack(spacing: 5) {
            if run.status == .running {
                Circle().fill(DS.Status.warn).frame(width: 6, height: 6)
            }
            Text(run.status.rawValue)
                .font(DS.mono(11, weight: .medium))
                .tracking(0.02 * 11)
                .foregroundStyle(statusColor)
        }
    }

    private var commitsString: String {
        run.commits.isEmpty ? "—" : "\(run.commits.count) commit\(run.commits.count == 1 ? "" : "s")"
    }

    private var costString: String {
        run.cost.map { "$\($0 as NSDecimalNumber)" } ?? "—"
    }

    private var iconName: String {
        switch run.status {
        case .success:            return "checkmark.circle"
        case .failure, .timeout:  return "exclamationmark.triangle"
        case .running:            return "circle.dotted"
        case .orphaned:           return "questionmark.circle"
        case .rateLimited:        return "hourglass"
        case .skippedBudget:      return "pause.circle"
        case .skippedConcurrency: return "lock.circle"
        case .scheduled:          return "clock"
        }
    }

    private var iconColor: Color {
        switch run.status {
        case .success:                                   return DS.Status.ok
        case .failure, .timeout, .rateLimited:           return DS.Status.err
        case .running:                                   return DS.Status.warn
        default:                                         return DS.Ink.p3
        }
    }

    private var statusColor: Color {
        switch run.status {
        case .success:                                   return DS.Status.ok
        case .failure, .timeout, .rateLimited:           return DS.Status.err
        case .running:                                   return DS.Status.warn
        default:                                         return DS.Ink.p3
        }
    }
}
