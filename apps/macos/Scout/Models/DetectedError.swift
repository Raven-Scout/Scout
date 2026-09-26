import Foundation

struct DetectedError: Equatable, Hashable, Sendable, Codable {
    let line: Int
    let pattern: String
    let snippet: String
}
