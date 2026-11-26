import Foundation

/// Tests that make a window key and send it keys or copy from it take turns.
/// Suites run in parallel, so two such tests each made their own window key
/// and stole the other's key status mid-test: a Tab went to the wrong window
/// (`DisclosureRowFocusTests` failed beside `FocusContainerWindowTests`).
@MainActor
enum KeyWindowGate {
    private static var busy = false
    private static var waiting: [CheckedContinuation<Void, Never>] = []

    static func run<T>(_ body: @MainActor () async throws -> T) async rethrows -> T {
        if busy {
            await withCheckedContinuation { waiting.append($0) }
        } else {
            busy = true
        }
        // The next test takes over the turn directly; `busy` stays true.
        defer { if waiting.isEmpty { busy = false } else { waiting.removeFirst().resume() } }
        return try await body()
    }
}
