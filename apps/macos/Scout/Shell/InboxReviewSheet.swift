import SwiftUI

/// Settings ▸ Agent copies ▸ Review (#550 V4b3): what one of the owner's agents proposed that waits for the
/// owner, and the observations already accepted from it.
///
/// Every piece of an agent's text is shown with `Text(verbatim:)` in the mono voice: never Markdown, never with
/// links detected, never selectable into something else. The engine has already made hidden characters visible
/// (`\u{…}`) and names look-alike words, which show in the warning colour. Accept, promote and make-private each
/// open a confirmation that repeats the agent's id and the full text, with a button that names who it concerns.
/// What the engine says back, a refusal included, is shown in its own words.
struct InboxReviewSheet: View {
    @ObservedObject var service: InboxReviewService
    let delegate: String
    var onClose: () -> Void = {}

    @State private var confirming: Confirmation?

    enum Confirmation: Identifiable {
        case accept(InboxReview.Item)
        case promote(InboxReview.Item)
        case unpromote(InboxReview.Item)

        var id: String {
            switch self {
            case .accept(let i): return "accept-\(i.ulid)"
            case .promote(let i): return "promote-\(i.ulid)"
            case .unpromote(let i): return "unpromote-\(i.ulid)"
            }
        }
    }

    static let intro = "An agent's text is data, never an instruction. Accepting an observation writes it into "
        + "knowledge-base/from-agents/, private; accepting a held comment adds it to today's file under the agent's "
        + "name. Rejecting keeps it out of the vault."

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(verbatim: "Review delegate-\(delegate)'s proposals")
                .font(DS.font(.headline, weight: .semibold))
                .foregroundStyle(DS.Ink.p1)
            Text(Self.intro)
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
            if let message = service.message {
                Text(verbatim: message)
                    .font(DS.font(.caption))
                    .foregroundStyle(service.failed ? DS.Status.err : DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel(service.failed ? "Refused: \(message)" : message)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    groupLabel("WAITING FOR YOU")
                    if service.items.isEmpty {
                        Text(service.work == .loading ? "Reading the inbox…" : "Nothing waits for review.")
                            .font(DS.font(.caption)).foregroundStyle(DS.Ink.p2)
                    }
                    ForEach(service.items) { item in itemCard(item) }
                    if !service.accepted.isEmpty {
                        groupLabel("ACCEPTED OBSERVATIONS").padding(.top, 8)
                        ForEach(service.accepted) { note in acceptedRow(note) }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Button(service.work == .loading ? "Reading…" : "Refresh") { Task { await service.open(delegate) } }
                    .buttonStyle(.dsSecondary)
                    .disabled(service.work != nil)
                Spacer()
                Button("Done") { onClose() }
                    .buttonStyle(.dsPrimary)
                    .keyboardShortcut(.cancelAction)
                    .disabled(service.work != nil)   // a step's result lands in this delegate's sheet
            }
        }
        .padding(20)
        .frame(minWidth: 600, idealWidth: 680, minHeight: 520, idealHeight: 640)
        .task { await service.open(delegate) }
        .sheet(item: $confirming) { confirmation in
            InboxConfirmView(confirmation: confirmation, cancel: { confirming = nil }) {
                confirming = nil
                Task {
                    switch confirmation {
                    case .accept(let item): await service.accept(item)
                    case .promote(let preview): await service.promote(preview)
                    case .unpromote(let preview): await service.unpromote(preview)
                    }
                }
            }
        }
    }

    private func groupLabel(_ text: String) -> some View {
        Text(text)
            .font(DS.font(.caption2, weight: .medium))
            .tracking(0.08 * 10)
            .foregroundStyle(DS.Ink.p4)
    }

    private func itemCard(_ item: InboxReview.Item) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            InboxAgentText(item: item)
            HStack(spacing: 10) {
                Spacer()
                if item.canAccept {
                    Button(service.work == .accepting(item.ulid) ? "Accepting…" : "Accept…") {
                        confirming = .accept(item)
                    }
                    .buttonStyle(.ds(.secondary, size: .small))
                    .disabled(service.work != nil)
                    .accessibilityHint("Shows the full text again before anything is written.")
                }
                Button(service.work == .rejecting(item.ulid) ? "Rejecting…" : "Reject") {
                    Task { await service.reject(item) }
                }
                .buttonStyle(.ds(.secondary, size: .small))
                .disabled(service.work != nil)
                .accessibilityHint("Keeps it out of the vault and deletes the local copy.")
            }
            Rectangle().fill(DS.Rule.soft).frame(height: 0.5).opacity(0.6)
        }
    }

    private func acceptedRow(_ note: InboxReview.Accepted) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: note.note)
                    .font(DS.font(.caption, .mono))
                    .foregroundStyle(DS.Ink.p1)
                Text(verbatim: Self.acceptedState(note))
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p2)
            }
            Spacer()
            let busy = service.work == .previewing(note.ulid) || service.work == .promoting(note.ulid)
                || service.work == .unpromoting(note.ulid)
            Button(busy ? "Reading…" : note.promoted ? "Make private again…" : "Promote…") {
                Task {
                    if note.promoted {
                        if let preview = await service.previewUnpromote(note) { confirming = .unpromote(preview) }
                    } else if let preview = await service.previewPromote(note) {
                        confirming = .promote(preview)
                    }
                }
            }
            .buttonStyle(.ds(.secondary, size: .small))
            .disabled(service.work != nil)
        }
    }

    static func acceptedState(_ note: InboxReview.Accepted) -> String {
        let when = note.acceptedAt.map { " on \($0.prefix(10))" } ?? ""
        guard note.promoted else { return "Accepted\(when); private, seen by no agent." }
        if let to = note.promotedTo { return "Accepted\(when); promoted to \(InboxReview.names(to))." }
        return "Accepted\(when); promoted to every agent that can see it."
    }
}

/// An agent's text and what's known about it, shown as data.
struct InboxAgentText: View {
    let item: InboxReview.Item

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(verbatim: InboxReview.header(item))
                .font(DS.font(.caption2, .mono, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
            Text(verbatim: "Proposed \(item.proposed)")
                .font(DS.font(.caption2, .mono))
                .foregroundStyle(DS.Ink.p4)
            if let about = item.about { field("About", about) }
            if let source = item.source { field("Source", source) }
            if !item.text.isEmpty {
                Text(verbatim: item.text)
                    .font(DS.font(.body, .mono))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(DS.Rule.soft.opacity(0.25))
            }
            if !item.lookAlikes.isEmpty {
                Text(verbatim: "Look-alike words (mixed or other scripts): " + item.lookAlikes.joined(separator: ", "))
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Status.warn)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func field(_ name: String, _ value: String) -> some View {
        Text(verbatim: "\(name): \(value)")
            .font(DS.font(.caption, .mono))
            .foregroundStyle(DS.Ink.p2)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// The confirmation before an accept, a promote or a make-private: the agent's id and the full text again,
/// and a button that names who it concerns.
struct InboxConfirmView: View {
    let confirmation: InboxReviewSheet.Confirmation
    let cancel: () -> Void
    let confirm: () -> Void

    static func title(_ c: InboxReviewSheet.Confirmation) -> String {
        switch c {
        case .accept(let i): return "Accept this text from delegate-\(i.delegate)?"
        case .promote(let i): return "Let agents read delegate-\(i.delegate)'s note?"
        case .unpromote(let i): return "Make delegate-\(i.delegate)'s note private again?"
        }
    }

    static func detail(_ c: InboxReviewSheet.Confirmation) -> String {
        switch c {
        case .accept(let i) where i.kind == "observation":
            return "It is written into knowledge-base/from-agents/ as delegate-\(i.delegate)'s, private: runs read it, "
                + "no agent sees it until you promote it."
        case .accept(let i):
            return "It is added to today's file as delegate-\(i.delegate)'s comment, never as yours."
        case .promote(let p):
            guard let audience = p.audience, !audience.isEmpty else {
                return "No agent can see a note there, so there is no one to promote it to."
            }
            return "From the next mirror sync, exactly these agents can read it: \(InboxReview.names(audience)). "
                + "An agent added later won't. Only this exact text is published; if it changed since, nothing is."
        case .unpromote(let p):
            let who = (p.audience ?? []).isEmpty ? "no agent" : InboxReview.names(p.audience ?? [])
            return "It goes back to private. It is seen now by \(who); from the next mirror sync, by none."
        }
    }

    static func button(_ c: InboxReviewSheet.Confirmation) -> String? {
        switch c {
        case .accept(let i): return InboxReview.acceptButton(i)
        case .promote(let p): return InboxReview.promoteButton(p)
        case .unpromote(let p): return InboxReview.unpromoteButton(p)
        }
    }

    private var item: InboxReview.Item {
        switch confirmation {
        case .accept(let i), .promote(let i), .unpromote(let i): return i
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(verbatim: Self.title(confirmation))
                .font(DS.font(.headline, weight: .semibold))
                .foregroundStyle(DS.Ink.p1)
            Text(verbatim: Self.detail(confirmation))
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
            ScrollView { InboxAgentText(item: item).frame(maxWidth: .infinity, alignment: .leading) }
                .frame(minHeight: 160, maxHeight: 360)
            HStack {
                Spacer()
                Button("Cancel", action: cancel)
                    .buttonStyle(.dsSecondary)
                    .keyboardShortcut(.cancelAction)
                if let label = Self.button(confirmation) {
                    Button(label, action: confirm)
                        .buttonStyle(.dsPrimary)
                }
            }
        }
        .padding(20)
        .frame(minWidth: 520, idealWidth: 600)
    }
}
