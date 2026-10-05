import Testing
import Foundation
@testable import Scout

// `.serialized`: `defaults(_:)` below relies on `register(defaults:)`'s
// process-global registration domain (see its doc comment) and resets that
// domain on every call, including the "unset" ones — correctness depends on
// no other call interleaving between one test's reset and its own read, so
// these tests must run one at a time, not concurrently.
@Suite("AppState.resolveScoutDirectory", .serialized)
struct AppStateVaultResolutionTests {
    let home = URL(fileURLWithPath: "/Users/alex")

    /// Builds a `UserDefaults` that answers `string(forKey: "scoutDataDir")`
    /// with `value` (or as unset, when `value` is `nil`) — without ever
    /// writing a plist to the real `~/Library/Preferences`.
    ///
    /// This is deliberately `register(defaults:)`, not `set(_:forKey:)`, and
    /// deliberately resets the key on *every* call rather than only when
    /// `value` is non-nil. Two things had to be verified by hand before
    /// relying on this (see the fix-round report for the repro scripts):
    ///
    /// 1. `register(defaults:)` is pure in-memory and never touches disk —
    ///    true regardless of suite name.
    /// 2. It is *not* scoped to the suite name the way `set(_:forKey:)` is —
    ///    it merges into a single **process-global** registration domain
    ///    shared by every `UserDefaults` instance. Two different suites
    ///    registering different values for the same key and reading back
    ///    both see whichever value was registered *last*, process-wide —
    ///    which is exactly why the first version of this fix (registering
    ///    only when `value != nil`) made `pointerVaultIsSecond()` and the
    ///    other "nil" tests observe `userDefaultWinsAndExpandsTilde()`'s
    ///    `"~/Custom"` leaking in from whichever test happened to run
    ///    earlier in the process.
    ///
    /// Resetting the shared key on *every* call (to `""` — treated as unset
    /// by `resolveScoutDirectory`'s `!raw.isEmpty` check — when there's no
    /// real value) closes that gap: as long as nothing else interleaves
    /// between this reset and the read that follows it (`.serialized` on the
    /// suite guarantees that), each test only ever observes its own value.
    func defaults(_ value: String?) -> UserDefaults {
        let d = UserDefaults(suiteName: "AppStateVaultResolutionTests-\(UUID().uuidString)")!
        d.register(defaults: ["scoutDataDir": value ?? ""])
        return d
    }

    let pointer = EnginePointer(schemaVersion: 1, version: "0.10.0", engineRoot: "/e", python: "/p", scoutctl: "/s",
                                vault: "/Users/alex/Vaults/Work", managedBy: "scout-app", writtenAt: "")

    @Test func userDefaultWinsAndExpandsTilde() {
        #expect(AppState.resolveScoutDirectory(defaults: defaults("~/Custom"), pointer: pointer, home: home).path == "/Users/alex/Custom")
    }
    @Test func pointerVaultIsSecond() {
        #expect(AppState.resolveScoutDirectory(defaults: defaults(nil), pointer: pointer, home: home).path == "/Users/alex/Vaults/Work")
    }
    @Test func blankDefaultFallsThrough() {
        #expect(AppState.resolveScoutDirectory(defaults: defaults("  "), pointer: nil, home: home).path == "/Users/alex/Scout")
    }

    @Test func pointerVaultEmptyIsTreatedAsUnset() {
        let blankVaultPointer = EnginePointer(schemaVersion: 1, version: "0.10.0", engineRoot: "/e", python: "/p", scoutctl: "/s",
                                               vault: "", managedBy: "scout-app", writtenAt: "")
        #expect(AppState.resolveScoutDirectory(defaults: defaults(nil), pointer: blankVaultPointer, home: home).path == "/Users/alex/Scout")
    }

    @Test func userDefaultAbsolutePathIsUsedAsIs() {
        #expect(AppState.resolveScoutDirectory(defaults: defaults("/Users/alex/Vaults/Home"), pointer: pointer, home: home).path == "/Users/alex/Vaults/Home")
    }

    /// A user-typed relative path would resolve against the process's cwd —
    /// treated as unset, so the pointer vault (then `~/Scout`) wins.
    @Test(arguments: ["Vaults/Work", "Scout", "./Scout", "~alex/Scout"])
    func userDefaultRelativePathIsTreatedAsUnset(raw: String) {
        #expect(AppState.resolveScoutDirectory(defaults: defaults(raw), pointer: pointer, home: home).path == "/Users/alex/Vaults/Work")
        #expect(AppState.resolveScoutDirectory(defaults: defaults(raw), pointer: nil, home: home).path == "/Users/alex/Scout")
    }

    @Test func pointerVaultRelativePathIsTreatedAsUnset() {
        let relativeVaultPointer = EnginePointer(schemaVersion: 1, version: "0.10.0", engineRoot: "/e", python: "/p", scoutctl: "/s",
                                                  vault: "Vaults/Work", managedBy: "scout-app", writtenAt: "")
        #expect(AppState.resolveScoutDirectory(defaults: defaults(nil), pointer: relativeVaultPointer, home: home).path == "/Users/alex/Scout")
    }
}
