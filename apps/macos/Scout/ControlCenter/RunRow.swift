import SwiftUI

/// Editorial run row: status icon → name/when → status → reveal chevron.
/// No boxes; hairline rules do the separation. The commits and cost columns
/// went in card T2: commits load only when the detail opens and runs rarely
/// carry a cost, so both only ever showed dashes. CC-8:
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
                .font(DS.font(.label))
                .foregroundStyle(iconColor)
                .frame(width: 20, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(run.displayName)
                        .font(DS.font(.body, weight: .medium))
                        .foregroundStyle(DS.Ink.p1)
                    if run.showsManualBadge {
                        manualBadge
                    }
                    ForEach(ProtectionBadge.kinds(for: run), id: \.title) { ProtectionBadge(kind: $0) }
                }
                Text(run.startedAt.formatted(date: .abbreviated, time: .shortened))
                    .font(DS.font(.caption, .mono))
                    .foregroundStyle(DS.Ink.p4)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            statusCell
                .frame(width: 100, alignment: .leading)
            Image(systemName: "chevron.right")
                .font(DS.font(.caption2, weight: .medium))
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
        Text("manual").dsTag(.mono)
    }

    private var statusCell: some View {
        HStack(spacing: 5) {
            if run.status == .running {
                Circle().fill(DS.Status.warn).frame(width: 6, height: 6)
            }
            Text(run.status.displayName)
                .font(DS.font(.caption, weight: .medium))
                .foregroundStyle(statusColor)
                .help(run.status.reason ?? run.status.displayName)
        }
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
    nonisolated enum Kind: Equatable, Sendable {
        /// What the ledger says about write protection, in the owner's words.
        case unprotected(String?)
        /// Scout put the file back (`restored`), or couldn't.
        case fileChanged(restored: Bool)
        /// The check after the run didn't run (`error` / `unchecked`).
        case unchecked

        var title: String {
            switch self {
            case .unprotected: return "Unprotected"
            case .fileChanged(let restored): return restored ? "File put back" : "Protected file changed"
            case .unchecked: return "Files not checked"
            }
        }
    }
    let kind: Kind

    /// Every badge a run earns, worst first: a run can both run unprotected and have a file put back.
    static func kinds(for run: Run) -> [Kind] {
        var out: [Kind] = []
        if run.changedProtectedFile { out.append(.fileChanged(restored: run.protection?.integrity == "restored")) }
        if run.protectedFilesUnchecked { out.append(.unchecked) }
        if run.ranUnprotected {
            out.append(.unprotected(RunOutcomeDisplay.sandbox(run.protection?.sandbox,
                                                             source: run.protection?.sandboxSource)))
        }
        return out
    }

    var body: some View {
        DSPill(title, tone: tone, symbol: symbol)
            .help(help)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(help)
    }

    var title: String { kind.title }

    var help: String {
        switch kind {
        case .unprotected(let line):
            return (line ?? "Write protection off") + ". This run could change Scout's own files."
        case .fileChanged(true):
            return "This run changed a protected file, and Scout put it back. Open the run to see the change."
        case .fileChanged(false):
            return "This run changed a protected file, and Scout couldn't put it back. Open the run to see what to do."
        case .unchecked:
            return "Scout couldn't check this run's protected files afterwards, so it doesn't know whether it changed one."
        }
    }

    private var symbol: String {
        switch kind {
        case .unprotected: return "shield.slash"
        case .fileChanged: return "exclamationmark.shield.fill"
        case .unchecked: return "questionmark.diamond"
        }
    }

    private var tone: DS.PillTone {
        switch kind {
        case .unprotected, .unchecked: return .warn
        case .fileChanged: return .err
        }
    }
}
