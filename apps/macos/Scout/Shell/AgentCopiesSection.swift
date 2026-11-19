import SwiftUI

/// Settings ▸ Agent copies (#550): each delegate mirror — a filtered copy of the vault one of the
/// owner's own agents reads off this Mac — with its remote, last sync, why a sync was refused, and
/// what the copy held. Read-only, apart from the two commands the CLI already gives the owner:
/// Preview (`delegate preview`, writes nothing) and Sync now (`delegate sync`, what the schedule
/// tick runs anyway). Linking, unlinking, keys and re-signing stay in Terminal.
///
/// Hidden until the engine reports a delegate: most vaults link no agent.
struct AgentCopiesSection: View {
    @ObservedObject var service: DelegateStatusService
    /// Testing seam: what "now" means for "synced 15 minutes ago".
    var now: () -> Date = Date.init

    var body: some View {
        // A VStack, not a Group: a Group with nothing in it hands `.task` to no view, so the first read never ran.
        VStack(alignment: .leading, spacing: 0) {
            if let status = service.status {
                let model = AgentCopiesModel(status: status, results: service.results, now: now())
                if model.isVisible { content(model) }
            } else if service.showsErrorCard, let error = service.lastError {
                // No status, but this vault has agent copies: say why (an engine too old for the card, a failed
                // read). A vault with none never sees the card, whatever the error.
                labelled {
                    SettingsCard {
                        SettingsRow(title: "Agent copies", help: error) { refreshButton }
                    }
                }
            }
        }
        // On its own the card only reads.
        .task { if service.status == nil && service.work == nil { await service.refresh() } }
    }

    @ViewBuilder
    private func content(_ model: AgentCopiesModel) -> some View {
        labelled {
            SettingsCard {
                SettingsRow(title: "Agent copies", help: service.lastError ?? AgentCopiesModel.intro) { refreshButton }
                if let problem = model.stateProblem { problemRow(problem.summary, problem.fix) }
                ForEach(model.rows) { row in delegateRow(row) }
            }
        }
    }

    private func labelled<Content: View>(@ViewBuilder _ card: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("AGENT COPIES")
                .font(DS.sans(10, weight: .medium))
                .tracking(0.08 * 10)
                .foregroundStyle(DS.Ink.p4)
                .padding(.horizontal, 4)
            card()
        }
        .padding(.bottom, 22)
    }

    private var refreshButton: some View {
        Button(service.work == .checking ? "Checking…" : "Refresh") { Task { await service.refresh() } }
            .buttonStyle(.plainHit)
            .font(DS.sans(12))
            .disabled(service.work != nil)
    }

    private func delegateRow(_ row: AgentCopiesModel.Row) -> some View {
        SettingsRow(title: row.id, help: row.help) {
            HStack(spacing: 10) {
                Text(row.value)
                    .font(DS.sans(12, weight: .medium))
                    .foregroundStyle(Self.color(row.tone))
                    .multilineTextAlignment(.trailing)
                if row.offersPreview {
                    Button(service.work == .previewing(row.id) ? "Previewing…" : "Preview") {
                        Task { await service.preview(row.id) }
                    }
                    .buttonStyle(.plainHit)
                    .font(DS.sans(12))
                    .disabled(service.work != nil || service.stale)
                    .accessibilityHint("Shows what a sync would put in \(row.id)'s copy. Changes nothing.")
                }
                if row.offersSync {
                    Button(service.work == .syncing(row.id) ? "Syncing…" : "Sync now") {
                        Task { await service.sync(row.id) }
                    }
                    .buttonStyle(.plainHit)
                    .font(DS.sans(12))
                    .disabled(service.work != nil || service.stale)
                    .accessibilityHint("Pushes a new copy to \(row.id)'s mirror if the vault changed what it may see.")
                }
            }
        }
    }

    private func problemRow(_ summary: String, _ fix: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(DS.Status.warn)
                    .accessibilityLabel("Warning")
                VStack(alignment: .leading, spacing: 4) {
                    Text(summary).font(DS.sans(12.5, weight: .medium)).foregroundStyle(DS.Ink.p1)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(fix).font(DS.sans(11.5)).foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.vertical, 12)
            Rectangle().fill(DS.Rule.soft).frame(height: 0.5).opacity(0.6)
        }
    }

    static func color(_ tone: AgentCopiesModel.Tone) -> Color {
        switch tone {
        case .ok: return DS.Status.ok
        case .warn: return DS.Status.warn
        case .bad: return DS.Status.err
        case .neutral: return DS.Ink.p2
        }
    }
}
