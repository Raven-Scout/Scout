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
            if !proposal.bodyBlocks.isEmpty {
                ProposalBodyView(blocks: proposal.bodyBlocks)
            }
            if proposal.isAwaitingDecision {
                actions
            }
            if let errorText {
                Label(errorText, systemImage: "exclamationmark.triangle.fill")
                    .font(DS.sans(11))
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
                        .font(DS.mono(11))
                        .foregroundStyle(DS.Ink.p4)
                }
                Text(proposal.title)
                    .font(DS.serif(17, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            ProposalStatusPill(status: proposal.status)
        }
    }

    // MARK: - Actions

    private var actions: some View {
        HStack(spacing: 6) {
            actButton("Approve", systemImage: "checkmark", decision: .approve, primary: true)
            actButton("Decline", systemImage: "xmark", decision: .decline, primary: false)
            Spacer(minLength: 0)
        }
        .padding(.top, 2)
    }

    @ViewBuilder
    private func actButton(
        _ label: String,
        systemImage: String,
        decision: ProposalDecision,
        primary: Bool
    ) -> some View {
        let isBusy = inFlight == decision
        Button { decide(decision) } label: {
            HStack(spacing: 5) {
                if isBusy {
                    ProgressView().controlSize(.small).frame(width: 12, height: 12)
                } else {
                    Image(systemName: systemImage).font(.system(size: 10))
                }
                Text(label).font(DS.sans(11.5, weight: .medium))
            }
            .foregroundStyle(primary ? DS.Status.ok : DS.Ink.p3)
            .padding(.horizontal, 12)
            .frame(height: 26)
            .background {
                RoundedRectangle(cornerRadius: 5)
                    .fill(DS.Paper.raised)
                    .overlay(
                        RoundedRectangle(cornerRadius: 5)
                            .strokeBorder(primary ? DS.Status.ok.opacity(0.4) : DS.Rule.hard, lineWidth: 0.5)
                    )
            }
        }
        .buttonStyle(.plainHit)
        .disabled(inFlight != nil)
        .onHover { hovering in
            if hovering, inFlight == nil { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
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
