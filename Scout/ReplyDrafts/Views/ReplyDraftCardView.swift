import SwiftUI
import AppKit

/// One reply draft rendered as an editorial card: heading (tag + recipient +
/// subject + channel/status chips), the prepared reply body (selectable, ready
/// to copy), and actions. The app **never sends** — Copy puts the text on the
/// pasteboard, Open thread opens the original conversation, and Mark sent /
/// Dismiss only flip the file's `status:`.
struct ReplyDraftCardView: View {
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

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            metaLine
            bodyPanel
            if draft.isAwaitingAction && !draft.inputs.isEmpty {
                inputsSection
            }
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

    // MARK: - Actions

    private var actions: some View {
        HStack(spacing: 6) {
            actButton(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc") {
                copyBody()
            }
            if !draft.threadRef.isEmpty {
                actButton("Open thread", systemImage: "arrow.up.right.square") {
                    openThread()
                }
            }
            Spacer(minLength: 0)
            if draft.isAwaitingAction {
                statusButton("Mark sent", systemImage: "paperplane", action: .markSent, tint: DS.Status.ok)
                statusButton("Dismiss", systemImage: "xmark", action: .dismiss, tint: DS.Ink.p3)
            } else {
                statusButton("Reopen", systemImage: "arrow.uturn.backward", action: .reopen, tint: DS.Ink.p3)
            }
        }
        .padding(.top, 2)
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
