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
    @EnvironmentObject var vaultUndo: VaultUndoController
    @Environment(\.undoManager) private var undoManager

    @State private var resolvedExpanded = false

    var body: some View {
        ScrollView {
            // Deliberately VStack, not LazyVStack (#83, third occurrence).
            // Same non-convergent height-estimation loop fixed in
            // ActionItemsView (#84) and SectionView (#86): a lazy stack under
            // this width-constraining frame chain wedges layout at 100% CPU
            // with enough variable-height content, and proposals are unbounded
            // markdown — ProposalsDocumentService parses every .md in the
            // folder with no cap. All of it is materialized up front anyway,
            // so laziness buys nothing. The .leading alignment on the
            // maxWidth-920 frame below is now load-bearing: a plain VStack
            // hugs its widest child, where the width-greedy LazyVStack made
            // the horizontal alignment moot.
            VStack(alignment: .leading, spacing: 16) {
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
                    NSWorkspace.shared.activateFileViewerSelecting([docService.directoryURL])
                } label: {
                    Image(systemName: "folder")
                }
                .help("Reveal the dreaming-proposals folder in Finder")
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
                    .glossaryHelp(.proposal)
                Spacer(minLength: 0)
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
        case 0:  return "Changes Scout suggests to how it works. Nothing is waiting for your decision."
        case 1:  return "1 suggested change is waiting for your decision. If you approve it, the next dreaming run makes the change."
        default: return "\(pending) suggested changes are waiting for your decision. If you approve one, the next dreaming run makes the change."
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
                message: "No suggested changes yet. Scout's dreaming runs suggest them after reading your feedback. If they are kept somewhere else, choose that folder in Settings."
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
        // Show the file as it is now, whether the write worked or not (C10).
        defer { docService.reload() }
        let writer = writerBox.writer
        guard let change = try await writer.decide(
            decision,
            fileURL: proposal.fileURL,
            label: proposal.title
        ) else { return }
        vaultUndo.register(
            .restoring(change) { change, direction in try await writer.restore(change, direction) },
            with: undoManager
        ) { [docService] in docService.reload() }
    }
}
