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
                .font(DS.sans(12))
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
                    if run.changedProtectedFile {
                        ProtectionBadge(kind: .fileChanged(restored: run.protection?.integrity == "restored"))
                    } else if run.ranUnprotected {
                        ProtectionBadge(kind: .unprotected(RunOutcomeDisplay.sandbox(run.protection?.sandbox,
                                                                                     source: run.protection?.sandboxSource)))
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
                .font(DS.sans(10, weight: .medium))
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
        .onKeyWindowHover { hovering = $0 }
        .keyWindowPointingHand()
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
            Text(run.status.displayName)
                .font(DS.sans(11, weight: .medium))
                .foregroundStyle(statusColor)
                .help(run.status.reason ?? run.status.displayName)
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
        case .skippedConcurrency, .skippedLease: return "lock.circle"
        case .skippedGates, .skippedSandbox: return "shield.slash"
        case .skippedRuntime:     return "nosign"
        case .skippedRedundant:   return "arrow.uturn.right.circle"
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

/// A run's protection, when it isn't the normal "write protection on, nothing
/// changed": it ran without write protection, or the check after it found a
/// protected file it had changed (card C9, from the run ledger).
struct ProtectionBadge: View {
    enum Kind: Equatable {
        /// What the ledger says about write protection, in the owner's words.
        case unprotected(String?)
        /// Scout put the file back (`restored`), or couldn't.
        case fileChanged(restored: Bool)
    }
    let kind: Kind

    var body: some View {
        Label(title, systemImage: symbol)
            .labelStyle(.titleAndIcon)
            .font(DS.sans(10, weight: .medium))
            .foregroundStyle(color)
            .padding(.horizontal, 5)
            .padding(.vertical, 1.5)
            .background(RoundedRectangle(cornerRadius: 4).fill(color.opacity(0.12)))
            .fixedSize()
            .help(help)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(help)
    }

    var title: String {
        switch kind {
        case .unprotected: return "Unprotected"
        case .fileChanged(let restored): return restored ? "File put back" : "Protected file changed"
        }
    }

    var help: String {
        switch kind {
        case .unprotected(let line):
            return (line ?? "Write protection off") + ". This run could change Scout's own files."
        case .fileChanged(true):
            return "This run changed a protected file, and Scout put it back. Open the run to see the change."
        case .fileChanged(false):
            return "This run changed a protected file, and Scout couldn't put it back. Open the run to see what to do."
        }
    }

    private var symbol: String {
        switch kind {
        case .unprotected: return "shield.slash"
        case .fileChanged: return "exclamationmark.shield.fill"
        }
    }

    private var color: Color {
        switch kind {
        case .unprotected: return DS.Status.warn
        case .fileChanged: return DS.Status.err
        }
    }
}
