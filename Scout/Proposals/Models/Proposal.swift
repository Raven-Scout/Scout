import Foundation

/// A single dreaming proposal parsed from `dreaming-proposals.md`.
///
/// Each proposal is a `### <code> — <title>` section followed by a
/// `**Status:**` line and free-form markdown body. `headingLine` is the exact
/// `### …` source line; it is both the stable identity for SwiftUI and the
/// match key the writer uses to locate the section when flipping status.
nonisolated struct Proposal: Identifiable, Equatable, Sendable {
    /// Exact `### …` heading line, verbatim from the file.
    let headingLine: String
    /// Display code lifted from the heading (e.g. `P-2026-06-13-01`, or a bare
    /// date for template-style headings). Empty if the heading has no ` — `.
    let code: String
    /// Title text after the ` — ` separator (or the whole heading if none).
    let title: String
    /// Parsed status.
    let status: ProposalStatus
    /// Everything in the section except the heading and the `**Status:**` line.
    let bodyMarkdown: String

    var id: String { headingLine }

    var isAwaitingDecision: Bool { status.isAwaitingDecision }

    /// Structured body blocks for rendering (prose paragraphs + code blocks).
    var bodyBlocks: [ProposalBodyBlock] { ProposalBodyBlock.blocks(from: bodyMarkdown) }
}
