import Testing
import Foundation
@testable import Scout

@Suite("EngineUpgrader")
struct EngineUpgraderTests {
    let fm = FileManager.default

    func layout() throws -> EngineLayout {
        let l = EngineLayout(home: fm.temporaryDirectory.appendingPathComponent("upgrader-\(UUID().uuidString)"))
        try fm.createDirectory(at: l.engineDir, withIntermediateDirectories: true)
        try fm.createDirectory(at: l.venvDir, withIntermediateDirectories: true)
        return l
    }

    func release(_ v: String) -> EngineRelease { .init(schemaVersion: 1, engine: .init(repo: "x", version: v, tag: "v\(v)", commit: ""), uv: .init(version: "0", sha256: [:])) }
    func install(_ v: String, l: EngineLayout) -> EngineInstall { .init(root: l.engineRoot(version: v), scoutctl: l.scoutctl(version: v), python: nil, version: v, vault: nil) }

    @Test func needsUpgradeOnlyForManagedAndBehind() throws {
        let l = try layout()
        defer { try? fm.removeItem(at: l.home) }
        let up = EngineUpgrader(layout: l, release: release("0.11.0"))
        #expect(up.needsUpgrade(state: .managed(install("0.10.0", l: l), vaultBootstrapped: true)))
        #expect(!up.needsUpgrade(state: .managed(install("0.11.0", l: l), vaultBootstrapped: true)))
        #expect(!up.needsUpgrade(state: .external(install("0.9.0", l: l), .devCheckout)))
        #expect(!up.needsUpgrade(state: .notInstalled))
    }

    @Test func garbageCollectKeepsCurrentAndOnePrevious() throws {
        let l = try layout()
        defer { try? fm.removeItem(at: l.home) }
        for v in ["0.9.0", "0.10.0", "0.11.0"] {
            try fm.createDirectory(at: l.engineRoot(version: v), withIntermediateDirectories: true)
            try fm.createDirectory(at: l.venv(version: v), withIntermediateDirectories: true)
        }
        try fm.createSymbolicLink(at: l.currentEngineLink, withDestinationURL: l.engineRoot(version: "0.11.0"))
        let removed = try EngineUpgrader(layout: l, release: release("0.11.0")).garbageCollect(keeping: "0.11.0")
        #expect(removed == ["0.9.0"])
        #expect(fm.fileExists(atPath: l.engineRoot(version: "0.10.0").path))
        #expect(!fm.fileExists(atPath: l.venv(version: "0.9.0").path))
        #expect(fm.fileExists(atPath: l.currentEngineLink.path))
    }

    /// Defense in depth: `current` actually resolves to 0.9.0 (e.g. the
    /// symlink flip for a newer download never completed), but the caller
    /// passes the bundled release's version ("0.11.0") as `keeping` — a stale
    /// or simply wrong argument. The engine Scout is actually running must
    /// survive regardless of what the caller claims is current.
    @Test func garbageCollectProtectsTheActualCurrentTargetEvenWhenKeepingDisagrees() throws {
        let l = try layout()
        defer { try? fm.removeItem(at: l.home) }
        for v in ["0.8.0", "0.9.0", "0.10.0", "0.11.0"] {
            try fm.createDirectory(at: l.engineRoot(version: v), withIntermediateDirectories: true)
            try fm.createDirectory(at: l.venv(version: v), withIntermediateDirectories: true)
        }
        try fm.createSymbolicLink(at: l.currentEngineLink, withDestinationURL: l.engineRoot(version: "0.9.0"))
        let removed = try EngineUpgrader(layout: l, release: release("0.11.0")).garbageCollect(keeping: "0.11.0")
        #expect(!removed.contains("0.9.0"))
        #expect(fm.fileExists(atPath: l.engineRoot(version: "0.9.0").path))
        #expect(fm.fileExists(atPath: l.venv(version: "0.9.0").path))
    }

    /// The sort inside `garbageCollect` must never force-unwrap `EngineVersion`
    /// — an unparsable directory entry (stray file, `.DS_Store`, a leftover
    /// `.partial`-less junk dir) is simply excluded, not a crash.
    @Test func garbageCollectIgnoresUnparsableEntriesWithoutCrashing() throws {
        let l = try layout()
        defer { try? fm.removeItem(at: l.home) }
        for v in ["0.9.0", "0.10.0", "0.11.0"] {
            try fm.createDirectory(at: l.engineRoot(version: v), withIntermediateDirectories: true)
            try fm.createDirectory(at: l.venv(version: v), withIntermediateDirectories: true)
        }
        try fm.createDirectory(at: l.engineDir.appendingPathComponent("not-a-version"), withIntermediateDirectories: true)
        try "junk".write(to: l.engineDir.appendingPathComponent(".DS_Store"), atomically: true, encoding: .utf8)
        try fm.createSymbolicLink(at: l.currentEngineLink, withDestinationURL: l.engineRoot(version: "0.11.0"))
        let removed = try EngineUpgrader(layout: l, release: release("0.11.0")).garbageCollect(keeping: "0.11.0")
        #expect(removed == ["0.9.0"])
        #expect(fm.fileExists(atPath: l.engineDir.appendingPathComponent("not-a-version").path))
    }
}
