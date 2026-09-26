import Foundation

protocol ClockSource: Sendable {
    func now() -> Date
}

struct SystemClock: ClockSource {
    func now() -> Date { Date() }
}
