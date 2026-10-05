import Foundation

protocol ClockSource: Sendable {
    func now() -> Date
}

/// `nonisolated` so it can be a default argument (`clock: any ClockSource =
/// SystemClock()`) of a `@MainActor` initializer without an isolation warning.
nonisolated struct SystemClock: ClockSource {
    func now() -> Date { Date() }
}
