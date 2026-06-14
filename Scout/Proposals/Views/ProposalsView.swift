import SwiftUI

/// The Proposals section: dreaming-generated SKILL.md change proposals from
/// `dreaming-proposals.md`, with Approve / Decline actions on the ones still
/// awaiting a decision and a read-only archive of resolved ones.
///
/// Approve/Decline only flips the proposal's `**Status:**` line and commits the
/// file — the next dreaming run is what actually applies the SKILL.md change.
struct ProposalsView: View {
    @EnvironmentObject var docService: ProposalsDocumentService
    @EnvironmentObject var writerBox: ProposalsWriterBox

    @State private var resolvedExpanded = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                header
                content
            }
            .frame(maxWidth: 920, alignment: .leading)
            .padding(.horizontal, 42)
            .padding(.top, 28)
            .padding(.bottom, 64)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .scrollIndicators(.visible)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Paper.base)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([docService.fileURL])
                } label: {
                    Image(systemName: "folder")
                }
                .help("Reveal dreaming-proposals.md in Finder")
            }
        }
        .onAppear { docService.load() }
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 14) {
                Text("Proposals")
                    .font(DS.serif(28, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Spacer(minLength: 0)
                Text("repo ~/Scout")
                    .font(DS.mono(12))
                    .foregroundStyle(DS.Ink.p4)
            }
            Text(subtitle)
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
        }
        .padding(.bottom, 6)
        .overlay(alignment: .bottom) { EditorialRule() }
    }

    private var subtitle: String {
        let pending = docService.pendingCount
        switch pending {
        case 0:  return "Dreaming-generated SKILL.md change proposals. Nothing awaiting your decision."
        case 1:  return "1 proposal awaiting your decision. Approving flips its status; the next dreaming run applies the change."
        default: return "\(pending) proposals awaiting your decision. Approving flips status; the next dreaming run applies the change."
        }
    }

    // MARK: - Content

    @ViewBuilder
    private var content: some View {
        switch docService.state {
        case .idle, .loading:
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.top, 60)
        case .missing:
            emptyState(
                icon: "tray",
                message: "No proposals file yet. Dreaming runs write proposals to dreaming-proposals.md when they process feedback."
            )
        case .failed(let err):
            Text("Couldn't load proposals: \(err)")
                .font(DS.sans(13))
                .foregroundStyle(DS.Status.err)
                .padding(.top, 24)
        case .loaded:
            loadedContent
        }
    }

    @ViewBuilder
    private var loadedContent: some View {
        let awaiting = docService.proposals.filter(\.isAwaitingDecision)
        let resolved = docService.proposals.filter { !$0.isAwaitingDecision }

        if docService.proposals.isEmpty {
            emptyState(
                icon: "tray",
                message: "No proposals right now. They'll appear here after a dreaming run files one."
            )
        } else {
            if awaiting.isEmpty {
                emptyState(
                    icon: "checkmark.circle",
                    message: "Nothing awaiting your decision. Resolved proposals are below."
                )
            }
            ForEach(awaiting) { proposal in
                ProposalCardView(proposal: proposal) { decision in
                    try await decide(proposal, decision)
                }
            }
            if !resolved.isEmpty {
                resolvedSection(resolved)
            }
        }
    }

    private func resolvedSection(_ resolved: [Proposal]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) { resolvedExpanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: resolvedExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                    Text("Resolved")
                        .font(DS.sans(11.5, weight: .semibold))
                        .tracking(0.06 * 11.5)
                    Text("\(resolved.count)")
                        .font(DS.mono(11))
                        .foregroundStyle(DS.Ink.p4)
                }
                .foregroundStyle(DS.Ink.p3)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plainHit)

            if resolvedExpanded {
                ForEach(resolved) { proposal in
                    ProposalCardView(proposal: proposal) { _ in }
                }
            }
        }
        .padding(.top, 12)
    }

    private func emptyState(icon: String, message: String) -> some View {
        VStack(spacing: 14) {
            Image(systemName: icon)
                .font(.largeTitle)
                .foregroundStyle(DS.Ink.p3)
            Text(message)
                .font(DS.serif(14))
                .foregroundStyle(DS.Ink.p2)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 440)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 60)
    }

    // MARK: - Actions

    private func decide(_ proposal: Proposal, _ decision: ProposalDecision) async throws {
        try await writerBox.writer.decide(
            decision,
            headingLine: proposal.headingLine,
            code: proposal.code
        )
        docService.reload()
    }
}
