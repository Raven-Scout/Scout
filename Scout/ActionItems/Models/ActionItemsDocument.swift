import Foundation

nonisolated struct ActionItemsDocument: Equatable, Hashable, Sendable {
    /// Calendar date (ET-local) parsed from the filename.
    let date: Date
    /// H1 title.
    let title: String
    /// Paragraphs between the H1 and the first H2.
    let preamble: [String]
    let sections: [ActionSection]
    let sourceURL: URL
    /// Cheap change signal — file size in bytes at the time of parse.
    let sourceBytes: Int
}
