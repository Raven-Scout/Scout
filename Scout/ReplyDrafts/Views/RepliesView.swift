import SwiftUI

/// The Reply Drafts section: prepared replies Scout owes (from the `drafts/`
/// directory), with Copy / Open thread / Mark sent / Dismiss on the ones still
/// awaiting action and a read-only archive of resolved ones.
///
/// The app never sends — it presents the drafted text for the user to send
/// himself and only flips the draft's `status:` field.
struct RepliesView: View {
    @EnvironmentObject var docService: ReplyDraftsDocumentService
    @EnvironmentObject var writerBox: ReplyDraftsWriterBox

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
                    NSWorkspace.shared.activateFileViewerSelecting([docService.directoryURL])
                } label: {
                    Image(systemName: "folder")
                }
                .help("Reveal the drafts folder in Finder")
            }
        }
        .onAppear { docService.load() }
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 14) {
                Text("Reply Drafts")
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
        case 0:  return "Replies Scout prepared for conversations you owe an answer. Nothing waiting — you're caught up."
        case 1:  return "1 reply prepared. Read it, copy or open the thread, send it yourself, then mark it sent. Scout never sends."
        default: return "\(pending) replies prepared. Read each, send it yourself, then mark it sent. Scout never sends."
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
                message: "No drafts folder found. Scout writes prepared replies into drafts/ during briefing and consolidation runs. You can point Scout at a different folder in Settings."
            )
        case .failed(let err):
            Text("Couldn't load reply drafts: \(err)")
                .font(DS.sans(13))
                .foregroundStyle(DS.Status.err)
                .padding(.top, 24)
        case .loaded:
            loadedContent
        }
    }

    @ViewBuilder
    private var loadedContent: some View {
        let awaiting = docService.drafts.filter(\.isAwaitingAction)
        let resolved = docService.drafts.filter { !$0.isAwaitingAction }

        if docService.drafts.isEmpty {
            emptyState(
                icon: "tray",
                message: "No reply drafts right now. They'll appear here after a briefing or consolidation run prepares one."
            )
        } else {
            if awaiting.isEmpty {
                emptyState(
                    icon: "checkmark.circle",
                    message: "Nothing waiting to send. Resolved drafts are below."
                )
            }
            ForEach(awaiting) { draft in
                ReplyDraftCardView(
                    draft: draft,
                    onAction: { action in try await apply(draft, action) },
                    onFill: { placeholder, value in try await fill(draft, placeholder, value) }
                )
            }
            if !resolved.isEmpty {
                resolvedSection(resolved)
            }
        }
    }

    private func resolvedSection(_ resolved: [ReplyDraft]) -> some View {
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
                ForEach(resolved) { draft in
                    ReplyDraftCardView(
                        draft: draft,
                        onAction: { action in try await apply(draft, action) },
                        onFill: { placeholder, value in try await fill(draft, placeholder, value) }
                    )
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

    private func apply(_ draft: ReplyDraft, _ action: DraftAction) async throws {
        try await writerBox.writer.apply(
            action,
            fileURL: draft.fileURL,
            label: draft.tag
        )
        docService.reload()
    }

    private func fill(_ draft: ReplyDraft, _ placeholder: String, _ value: String) async throws {
        try await writerBox.writer.fill(
            placeholder: placeholder,
            value: value,
            fileURL: draft.fileURL,
            label: draft.tag
        )
        docService.reload()
    }
}
