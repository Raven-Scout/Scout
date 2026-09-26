import Foundation

/// A single dreaming proposal, parsed from one file in the
/// `dreaming-proposals/` directory.
///
/// Each file has YAML frontmatter (`title`, `status`, `date`, `target`)
/// followed by the proposal body (Trigger / Proposed change / Rationale /
/// Evidence). `fileURL` is the stable identity for SwiftUI and the target the
/// writer rewrites when flipping `status`.
///
/// (Until 2026-05-02 proposals were `### …` sections inside a single
/// `dreaming-proposals.md`; the vault since splits them per file, and that
/// single file is now just an index. This model follows the per-file format.)
nonisolated struct Proposal: Identifiable, Equatable, Sendable {
    /// Absolute URL of the proposal's markdown file — stable identity + the
    /// file the writer rewrites.
    let fileURL: URL
    /// Date string from frontmatter `date:`, falling back to the filename's
    /// `YYYY-MM-DD` prefix. Shown as the card's code chip.
    let date: String
    /// Title from frontmatter `title:`, falling back to the filename stem.
    let title: String
    /// Parsed lifecycle status (frontmatter `status:`).
    let status: ProposalStatus
    /// Proposal body — everything after the frontmatter, with a leading
    /// duplicate `# …` heading stripped (the title shows in the card header).
    let bodyMarkdown: String

    var id: String { fileURL.path }

    /// Header chip — the date reads like the old `code` the card renders.
    var code: String { date }

    var isAwaitingDecision: Bool { status.isAwaitingDecision }

    /// Structured body blocks for rendering (prose paragraphs + code blocks).
    var bodyBlocks: [ProposalBodyBlock] { ProposalBodyBlock.blocks(from: bodyMarkdown) }
}
