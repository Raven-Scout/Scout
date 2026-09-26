// Scout/PerFileItems/Models/PerFileItem.swift
import Foundation

/// One per-file Wishlist/Research item: YAML frontmatter + markdown body.
nonisolated struct PerFileItem: Identifiable, Equatable, Sendable {
    let fileURL: URL          // stable identity + the file the writer rewrites
    let date: String          // frontmatter date: or filename YYYY-MM-DD prefix
    let title: String         // frontmatter title: or filename stem
    let status: ItemStatus
    let priority: ItemPriority
    let source: String?       // wishlist provenance (optional)
    let area: String?         // research grouping (optional)
    let bodyMarkdown: String

    var id: String { fileURL.path }
    var isActive: Bool { status.isActive }
    var bodyBlocks: [MarkdownBodyBlock] { MarkdownBodyBlock.blocks(from: bodyMarkdown) }
}
