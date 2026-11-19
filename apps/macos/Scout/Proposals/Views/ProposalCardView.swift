import SwiftUI

/// One proposal rendered as an editorial card: heading (code + title + status
/// pill), structured body, and — for proposals still awaiting a decision —
/// Approve / Decline actions. Owns its in-flight + error state so a slow or
/// failed write surfaces on the card itself.
struct ProposalCardView: View {
    let proposal: Proposal
    /// Performs the write. Throws so the card can show an inline error.
    let onDecide: @MainActor (ProposalDecision) async throws -> Void

    @State private var inFlight: ProposalDecision?
    @State private var errorText: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if let explanation = proposal.status.explanation() {
                Label(explanation, systemImage: explanationIcon)
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Ink.p2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !proposal.bodyBlocks.isEmpty {
                ProposalBodyView(blocks: proposal.bodyBlocks)
            }
            if proposal.isAwaitingDecision {
                actions
            }
            if let errorText {
                Label(errorText, systemImage: "exclamationmark.triangle.fill")
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Status.err)
            }
        }
        .editorialCard(padding: 18)
    }

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                if !proposal.code.isEmpty {
                    Text(proposal.code)
                        .font(DS.font(.caption, .mono))
                        .foregroundStyle(DS.Ink.p4)
                }
                Text(proposal.title)
                    .dsFont(.title, .serif, weight: .medium)
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            ProposalStatusPill(status: proposal.status)
        }
    }

    private var explanationIcon: String {
        if case .unknown = proposal.status { return "questionmark.circle" }
        return "clock"
    }

    // MARK: - Actions

    private var actions: some View {
        HStack(spacing: 6) {
            // An unknown status gets no primary Approve (review of #716):
            // approving tells the next dreaming run to apply the change.
            actButton(ProposalDecision.approve.actionName, systemImage: "checkmark", decision: .approve,
                      role: proposal.status.hasPrimaryApprove ? .primary : .secondary)
            actButton(ProposalDecision.decline.actionName, systemImage: "xmark", decision: .decline, role: .secondary)
            Spacer(minLength: 0)
        }
        .padding(.top, 2)
    }

    @ViewBuilder
    private func actButton(
        _ label: String,
        systemImage: String,
        decision: ProposalDecision,
        role: DS.ButtonRole
    ) -> some View {
        let isBusy = inFlight == decision
        Button { decide(decision) } label: {
            HStack(spacing: 5) {
                if isBusy {
                    ProgressView().controlSize(.small).frame(width: 12, height: 12)
                } else {
                    Image(systemName: systemImage)
                }
                Text(label)
            }
        }
        .buttonStyle(.ds(role, size: .small))
        .disabled(inFlight != nil)
        .keyWindowPointingHand(enabled: inFlight == nil)
        .accessibilityLabel("\(label) \(proposal.title)")
    }

    private func decide(_ decision: ProposalDecision) {
        inFlight = decision
        errorText = nil
        Task {
            do {
                try await onDecide(decision)
            } catch {
                errorText = "Couldn't update the file — \(error.localizedDescription)"
            }
            inFlight = nil
        }
    }
}
