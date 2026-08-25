import Foundation

/// A single prepared reply draft, parsed from one file in the `drafts/`
/// directory.
///
/// Scout (the plugin) detects an open conversational loop where the user owes
/// someone a reply, prepares the reply text, and writes it as `drafts/<TAG>.md`
/// with YAML frontmatter (`tag`, `channel`, `loop_type`, `to`, `thread_ref`,
/// `subject`, `status`, `created`, `context_answer_ref`) followed by the draft
/// body. `fileURL` is the stable identity for SwiftUI and the file the writer
/// rewrites when flipping `status`.
///
/// The app shows the draft so the user can read, copy, and **send it himself**;
/// it never sends and never creates a native draft.
nonisolated struct ReplyDraft: Identifiable, Equatable, Sendable {
    /// Absolute URL of the draft markdown file — stable identity + the file the
    /// writer rewrites.
    let fileURL: URL
    /// `tag:` — mirrors the action item `[#TAG]`; falls back to the filename stem.
    let tag: String
    /// `channel:` — where the reply is owed.
    let channel: DraftChannel
    /// `loop_type:` — `direct-debt` or `promise-answered` (verbatim).
    let loopType: String
    /// `to:` — recipient (name + address/handle when known).
    let to: String
    /// `cc:` — other thread recipients to keep on the reply (email/PR); nil if none.
    let cc: String?
    /// `thread_ref:` — link/permalink/thread id to the original conversation.
    let threadRef: String
    /// `subject:` — email subject or PR/issue title; nil for chat channels.
    let subject: String?
    /// Parsed lifecycle status (`status:`).
    let status: DraftStatus
    /// `created:` date, falling back to the filename's `YYYY-MM-DD` prefix.
    let created: String
    /// `context_answer_ref:` — for `promise-answered`, the answer that unblocked
    /// the reply; nil otherwise.
    let contextAnswerRef: String?
    /// The drafted reply body — the **sendable** message only (everything after
    /// the frontmatter and before the `<!-- scout:context -->` marker).
    let bodyMarkdown: String
    /// AI-generated one-paragraph summary of what the thread/topic is about,
    /// from the context block. nil when the draft carries no context block.
    let summary: String?
    /// Prior messages on the topic, from the context block — shown in the
    /// collapsible "Thread" section. Empty when none.
    let relatedMessages: [DraftMessage]

    var id: String { fileURL.path }

    var isAwaitingAction: Bool { status.isAwaitingAction }

    /// Fill-in slots (`[TBD: …]` markers) the user still needs to resolve.
    var inputs: [DraftInput] { DraftInput.extract(from: bodyMarkdown) }

    /// Header chip — the tag reads like a code label.
    var code: String { tag }

    /// Whether a subject line should be shown (channel uses one and it's set).
    var showsSubject: Bool { channel.usesSubject && (subject?.isEmpty == false) }
}
