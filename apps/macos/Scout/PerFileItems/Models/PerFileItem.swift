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
    /// `bodyMarkdown` split into blocks once, when the file is parsed — not
    /// on every card render (a research lane's body reached 220 KB).
    let bodyBlocks: [MarkdownBodyBlock]

    init(fileURL: URL, date: String, title: String, status: ItemStatus, priority: ItemPriority,
         source: String?, area: String?, bodyMarkdown: String) {
        self.fileURL = fileURL
        self.date = date
        self.title = title
        self.status = status
        self.priority = priority
        self.source = source
        self.area = area
        self.bodyMarkdown = bodyMarkdown
        self.bodyBlocks = MarkdownBodyBlock.blocks(from: bodyMarkdown)
    }

    var id: String { fileURL.path }
    var isActive: Bool { status.isActive }
}
