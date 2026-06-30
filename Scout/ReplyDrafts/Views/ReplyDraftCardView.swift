import SwiftUI
import AppKit

/// One reply draft rendered as an editorial card: heading (tag + recipient +
/// subject + channel/status chips), the prepared reply body (selectable, ready
/// to copy), and actions. The app **never sends** — Copy puts the text on the
/// pasteboard, Open thread opens the original conversation, and Mark sent /
/// Dismiss only flip the file's `status:`.
struct ReplyDraftCardView: View {
    @EnvironmentObject var chat: ReplyChatService
    let draft: ReplyDraft
    /// Performs a status write. Throws so the card can show an inline error.
    let onAction: @MainActor (DraftAction) async throws -> Void
    /// Fills a `[TBD: …]` placeholder with a value, writing it into the body.
    let onFill: @MainActor (_ placeholder: String, _ value: String) async throws -> Void

    @State private var inFlight: DraftAction?
    @State private var errorText: String?
    @State private var copied = false
    @State private var inputValues: [String: String] = [:]
    @State private var fillingID: String?
    @State private var summaryExpanded = false
    @State private var threadExpanded = false
    @State private var chatExpanded = false
    @State private var chatInput = ""
    @State private var confirmingSlack = false
    @State private var deliveryNote: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            metaLine
            bodyPanel
            contextSection
            if draft.isAwaitingAction && !draft.inputs.isEmpty {
                inputsSection
            }
            chatSection
            actions
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
                if !draft.code.isEmpty {
                    Text("#\(draft.code)")
                        .font(DS.mono(11))
                        .foregroundStyle(DS.Ink.p4)
                }
                Text(recipientLine)
                    .font(DS.serif(17, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                if let cc = draft.cc {
                    Text("Cc \(cc)")
                        .font(DS.sans(11.5))
                        .foregroundStyle(DS.Ink.p4)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if draft.showsSubject, let subject = draft.subject {
                    Text(subject)
                        .font(DS.sans(12.5))
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 6) {
                DraftStatusPill(status: draft.status)
                ChannelBadge(channel: draft.channel)
            }
        }
    }

    private var recipientLine: String {
        draft.to.isEmpty ? "Reply" : "To \(draft.to)"
    }

    // MARK: - Meta

    @ViewBuilder
    private var metaLine: some View {
        let bits = [draft.loopType.isEmpty ? nil : loopTypeLabel,
                    draft.created.isEmpty ? nil : "prepared \(draft.created)"]
            .compactMap { $0 }
        if !bits.isEmpty {
            Text(bits.joined(separator: " · "))
                .font(DS.sans(11))
                .foregroundStyle(DS.Ink.p4)
        }
    }

    private var loopTypeLabel: String {
        switch draft.loopType {
        case "direct-debt":      return "you owe a reply"
        case "promise-answered": return "promise now answerable"
        default:                 return draft.loopType
        }
    }

    // MARK: - Body

    private var bodyPanel: some View {
        Text(draft.bodyMarkdown.isEmpty ? "(empty draft)" : draft.bodyMarkdown)
            .font(DS.serif(14))
            .foregroundStyle(DS.Ink.p1)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)
            .padding(14)
            .background {
                RoundedRectangle(cornerRadius: 7)
                    .fill(DS.Paper.sunk)
                    .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
            }
    }

    // MARK: - Context (summary + thread)

    @ViewBuilder
    private var contextSection: some View {
        if draft.summary != nil || !draft.relatedMessages.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                if let summary = draft.summary {
                    DisclosureGroup(isExpanded: $summaryExpanded) {
                        Text(summary)
                            .font(DS.serif(13))
                            .foregroundStyle(DS.Ink.p2)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.top, 6)
                    } label: {
                        disclosureLabel("Summary", systemImage: "sparkles")
                    }
                }
                if !draft.relatedMessages.isEmpty {
                    DisclosureGroup(isExpanded: $threadExpanded) {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(draft.relatedMessages) { messageRow($0) }
                        }
                        .padding(.top, 8)
                    } label: {
                        disclosureLabel("Thread (\(draft.relatedMessages.count))", systemImage: "text.bubble")
                    }
                }
            }
            .tint(DS.Ink.p3)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: 7)
                    .fill(DS.Paper.sunk)
                    .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
            }
        }
    }

    private func disclosureLabel(_ title: String, systemImage: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: systemImage).font(.system(size: 11))
            Text(title)
                .font(DS.sans(11.5, weight: .semibold))
                .tracking(0.04 * 11.5)
        }
        .foregroundStyle(DS.Ink.p3)
        .contentShape(Rectangle())
    }

    private func messageRow(_ msg: DraftMessage) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(msg.sender)
                    .font(DS.sans(11.5, weight: .semibold))
                    .foregroundStyle(DS.Ink.p2)
                if !msg.date.isEmpty {
                    Text(msg.date)
                        .font(DS.mono(10.5))
                        .foregroundStyle(DS.Ink.p4)
                }
            }
            Text(msg.text)
                .font(DS.serif(12.5))
                .foregroundStyle(DS.Ink.p2)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Fill-in inputs

    private var inputsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Fill in before sending (\(draft.inputs.count))")
                .font(DS.sans(11, weight: .semibold))
                .tracking(0.06 * 11)
                .foregroundStyle(DS.Ink.p3)
            ForEach(draft.inputs) { input in
                VStack(alignment: .leading, spacing: 4) {
                    Text(input.prompt)
                        .font(DS.sans(12))
                        .foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 6) {
                        TextField("Your input…", text: binding(for: input.id), axis: .vertical)
                            .textFieldStyle(.roundedBorder)
                            .font(DS.sans(12.5))
                            .lineLimit(1...4)
                            .onSubmit { applyFill(input) }
                        Button { applyFill(input) } label: {
                            if fillingID == input.id {
                                ProgressView().controlSize(.small).frame(width: 12, height: 12)
                            } else {
                                Text("Fill in").font(DS.sans(11.5, weight: .medium))
                            }
                        }
                        .buttonStyle(.plainHit)
                        .disabled(trimmed(input.id).isEmpty || fillingID != nil)
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            RoundedRectangle(cornerRadius: 7)
                .fill(DS.Accent.wash)
                .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(DS.Accent.ink.opacity(0.25), lineWidth: 0.5))
        }
    }

    private func binding(for id: String) -> Binding<String> {
        Binding(get: { inputValues[id] ?? "" }, set: { inputValues[id] = $0 })
    }

    private func trimmed(_ id: String) -> String {
        (inputValues[id] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func applyFill(_ input: DraftInput) {
        let value = trimmed(input.id)
        guard !value.isEmpty, fillingID == nil else { return }
        fillingID = input.id
        errorText = nil
        Task {
            do {
                try await onFill(input.placeholder, value)
                inputValues[input.id] = nil
            } catch {
                errorText = "Couldn't fill in — \(error.localizedDescription)"
            }
            fillingID = nil
        }
    }

    // MARK: - AI assistant chat

    private var chatSection: some View {
        DisclosureGroup(isExpanded: $chatExpanded) {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(chat.messages(for: draft.tag)) { chatBubble($0) }
                HStack(spacing: 6) {
                    TextField("Ask about this topic…", text: $chatInput, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .font(DS.sans(12.5))
                        .lineLimit(1...5)
                        .onSubmit { sendChat() }
                    Button { sendChat() } label: {
                        if chat.isBusy(draft.tag) {
                            ProgressView().controlSize(.small).frame(width: 12, height: 12)
                        } else {
                            Image(systemName: "paperplane.fill").font(.system(size: 11))
                        }
                    }
                    .buttonStyle(.plainHit)
                    .disabled(chatInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || chat.isBusy(draft.tag))
                }
                .padding(.top, 4)
                Text("Runs on your Claude license · won't send anything")
                    .font(DS.sans(10))
                    .foregroundStyle(DS.Ink.p4)
            }
            .padding(.top, 8)
        } label: {
            disclosureLabel("Ask AI about this topic", systemImage: "bubble.left.and.bubble.right")
        }
        .tint(DS.Ink.p3)
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            RoundedRectangle(cornerRadius: 7)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        }
    }

    @ViewBuilder
    private func chatBubble(_ msg: ChatMessage) -> some View {
        let isUser = msg.role == .user
        HStack {
            if isUser { Spacer(minLength: 24) }
            Text(msg.text)
                .font(DS.serif(12.5))
                .foregroundStyle(msg.role == .error ? DS.Status.err : DS.Ink.p1)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(isUser ? DS.Accent.wash : DS.Paper.sunk)
                }
            if !isUser { Spacer(minLength: 24) }
        }
    }

    private func sendChat() {
        let text = chatInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !chat.isBusy(draft.tag) else { return }
        chatInput = ""
        Task { await chat.send(text: text, about: draft) }
    }

    // MARK: - Actions

    private var actions: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                actButton(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc") {
                    copyBody()
                }
                if !draft.threadRef.isEmpty {
                    actButton(draft.channel.openActionLabel, systemImage: "arrow.up.right.square") {
                        openThread()
                    }
                }
                Spacer(minLength: 0)
                if draft.isAwaitingAction {
                    deliveryButton
                    statusButton("Mark sent", systemImage: "checkmark", action: .markSent, tint: DS.Status.ok)
                    statusButton("Dismiss", systemImage: "xmark", action: .dismiss, tint: DS.Ink.p3)
                } else {
                    statusButton("Reopen", systemImage: "arrow.uturn.backward", action: .reopen, tint: DS.Ink.p3)
                }
            }
            if let deliveryNote {
                Text(deliveryNote)
                    .font(DS.sans(11))
                    .foregroundStyle(deliveryNote.hasPrefix("Failed") || deliveryNote.hasPrefix("Couldn't") ? DS.Status.err : DS.Status.ok)
            }
        }
        .padding(.top, 2)
        .confirmationDialog(
            "Send this reply to \(draft.to) via Slack?",
            isPresented: $confirmingSlack,
            titleVisibility: .visible
        ) {
            Button("Send via Slack", role: .destructive) { deliver(.slackSend) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This sends the message now — it can't be unsent.")
        }
    }

    /// Channel-conditional delivery button: Slack actually sends (after a
    /// confirm); email creates a Gmail draft to review and send from Gmail.
    @ViewBuilder
    private var deliveryButton: some View {
        let busy = chat.isDelivering(draft.tag)
        switch draft.channel {
        case .slack:
            Button { confirmingSlack = true } label: {
                chrome(label: "Send via Slack", systemImage: "paperplane.fill", tint: DS.Accent.ink, busy: busy)
            }
            .buttonStyle(.plainHit)
            .disabled(busy)
        case .email:
            Button { deliver(.gmailDraft) } label: {
                chrome(label: "Create Gmail draft", systemImage: "envelope", tint: DS.Accent.ink, busy: busy)
            }
            .buttonStyle(.plainHit)
            .disabled(busy)
        default:
            EmptyView()
        }
    }

    private func deliver(_ kind: ReplyChatService.DeliveryKind) {
        deliveryNote = nil
        Task {
            let result = await chat.deliver(kind, draft: draft)
            deliveryNote = result.message
            // A successful Slack send closes the loop; mark the draft sent.
            if result.ok && kind == .slackSend {
                try? await onAction(.markSent)
            }
        }
    }

    /// A local (non-writing) action button — Copy / Open thread.
    @ViewBuilder
    private func actButton(_ label: String, systemImage: String, perform: @escaping () -> Void) -> some View {
        Button(action: perform) {
            chrome(label: label, systemImage: systemImage, tint: DS.Ink.p3, busy: false)
        }
        .buttonStyle(.plainHit)
        .onHover { hovering in
            if hovering { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }

    /// A status-writing action button — Mark sent / Dismiss / Reopen.
    @ViewBuilder
    private func statusButton(_ label: String, systemImage: String, action: DraftAction, tint: Color) -> some View {
        let isBusy = inFlight == action
        Button { perform(action) } label: {
            chrome(label: label, systemImage: systemImage, tint: tint, busy: isBusy)
        }
        .buttonStyle(.plainHit)
        .disabled(inFlight != nil)
        .onHover { hovering in
            if hovering, inFlight == nil { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }

    private func chrome(label: String, systemImage: String, tint: Color, busy: Bool) -> some View {
        HStack(spacing: 5) {
            if busy {
                ProgressView().controlSize(.small).frame(width: 12, height: 12)
            } else {
                Image(systemName: systemImage).font(.system(size: 10))
            }
            Text(label).font(DS.sans(11.5, weight: .medium))
        }
        .foregroundStyle(tint)
        .padding(.horizontal, 12)
        .frame(height: 26)
        .background {
            RoundedRectangle(cornerRadius: 5)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(tint.opacity(0.4), lineWidth: 0.5))
        }
    }

    // MARK: - Behavior

    private func copyBody() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(draft.bodyMarkdown, forType: .string)
        copied = true
        Task {
            try? await Task.sleep(nanoseconds: 1_400_000_000)
            copied = false
        }
    }

    private func openThread() {
        guard let url = URL(string: draft.threadRef) else { return }
        NSWorkspace.shared.open(url)
    }

    private func perform(_ action: DraftAction) {
        inFlight = action
        errorText = nil
        Task {
            do {
                try await onAction(action)
            } catch {
                errorText = "Couldn't update the file — \(error.localizedDescription)"
            }
            inFlight = nil
        }
    }
}
