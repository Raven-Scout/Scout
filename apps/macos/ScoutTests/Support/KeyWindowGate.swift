import Foundation

/// Tests that make a window key and send it keys or copy from it take turns.
/// Suites run in parallel, so two such tests each made their own window key
/// and stole the other's key status mid-test: a Tab went to the wrong window
/// (`DisclosureRowFocusTests` failed beside `FocusContainerWindowTests`).
/// A `LiveAccessibilityTree` takes a turn too: it keeps a full main window
/// hosted with accessibility switched on app-wide for a whole test, and
/// Tab in `FocusContainerWindowTests` stopped reaching its list beside one.
@MainActor
enum KeyWindowGate {
    private static var busy = false
    private static var waiting: [CheckedContinuation<Void, Never>] = []

    static func run<T>(_ body: @MainActor () async throws -> T) async rethrows -> T {
        await acquire()
        defer { release() }
        return try await body()
    }

    /// Takes the turn for a holder whose span isn't one closure
    /// (`LiveAccessibilityTree`, from `init` to `close()`). Not reentrant:
    /// every `acquire()` is paired with exactly one `release()`.
    static func acquire() async {
        if busy {
            await withCheckedContinuation { waiting.append($0) }
        } else {
            busy = true
        }
    }

    static func release() {
        // The next test takes over the turn directly; `busy` stays true.
        if waiting.isEmpty { busy = false } else { waiting.removeFirst().resume() }
    }
}
