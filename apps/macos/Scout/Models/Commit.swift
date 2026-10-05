import Foundation

struct Commit: Identifiable, Equatable, Hashable, Sendable {
    let id: String          // full SHA
    let shortSHA: String
    let timestamp: Date
    let subject: String
    let filesChanged: Int
    let insertions: Int
    let deletions: Int
}
