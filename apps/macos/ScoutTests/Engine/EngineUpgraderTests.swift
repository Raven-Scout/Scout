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

    func release(_ v: String) -> EngineRelease { .init(schemaVersion: 2, version: v, engine: .init(version: v), uv: .init(version: "0", sha256: [:])) }
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

    /// Ruling 68: the vault switches (`bootstrap upgrade`, via the new venv)
    /// before Claude Code does — `registerWithClaudeCode` repoints `current`
    /// and updates the marketplace + plugin only after that.
    @Test func upgradeStepsBootstrapBeforeRegistering() {
        #expect(EngineUpgrader.upgradeSteps == [.ensureUv, .unpackEngine, .buildVenv, .bootstrapVault, .registerWithClaudeCode, .verify])
    }

    @Test func needsUpgradeComparesTheBundledVersionAgainstAManagedInstall() {
        let i = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil)
        #expect(EngineUpgrader.needsUpgrade(state: .managed(i, vaultBootstrapped: true), bundledVersion: "0.11.0"))
        #expect(!EngineUpgrader.needsUpgrade(state: .managed(i, vaultBootstrapped: true), bundledVersion: "0.10.0"))
        #expect(!EngineUpgrader.needsUpgrade(state: .managed(i, vaultBootstrapped: true), bundledVersion: "not-a-version"))
        let unversioned = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: nil, vault: nil)
        #expect(!EngineUpgrader.needsUpgrade(state: .managed(unversioned, vaultBootstrapped: true), bundledVersion: "0.11.0"))
    }

    func writeRegistry(_ l: EngineLayout, marketplacePath: String?, pluginVersion: String?) throws {
        try fm.createDirectory(at: l.claudePluginsDir, withIntermediateDirectories: true)
        if let marketplacePath {
            try #"{"scout-plugin": {"source": {"source": "directory", "path": "\#(marketplacePath)"}}}"#
                .write(to: l.claudePluginsDir.appendingPathComponent("known_marketplaces.json"), atomically: true, encoding: .utf8)
        }
        if let pluginVersion {
            try #"{"plugins": {"scout@scout-plugin": [{"version": "\#(pluginVersion)", "installPath": "\#(l.currentEngineLink.path)"}]}}"#
                .write(to: l.claudePluginsDir.appendingPathComponent("installed_plugins.json"), atomically: true, encoding: .utf8)
        }
    }

    /// Ruling 69 I1: after `bootstrap upgrade` rewrote the pointer to the new
    /// version, the switch is unfinished until `current` resolves to that
    /// root AND Claude Code's scout plugin is that version.
    @Test func unfinishedSwitchNeedsCurrentAndThePluginOnThePointersVersion() throws {
        let l = try layout()
        defer { try? fm.removeItem(at: l.home) }
        let up = EngineUpgrader(layout: l, release: release("0.11.0"))
        let state = EngineState.managed(install("0.11.0", l: l), vaultBootstrapped: true)
        for v in ["0.10.0", "0.11.0"] { try fm.createDirectory(at: l.engineRoot(version: v), withIntermediateDirectories: true) }

        #expect(up.hasUnfinishedSwitch(state: state))                     // no `current`, no plugin
        try fm.createSymbolicLink(at: l.currentEngineLink, withDestinationURL: l.engineRoot(version: "0.10.0"))
        try writeRegistry(l, marketplacePath: l.currentEngineLink.path, pluginVersion: "0.11.0")
        #expect(up.hasUnfinishedSwitch(state: state))                     // `current` still on the old root
        try fm.removeItem(at: l.currentEngineLink)
        try fm.createSymbolicLink(at: l.currentEngineLink, withDestinationURL: l.engineRoot(version: "0.11.0"))
        try writeRegistry(l, marketplacePath: nil, pluginVersion: "0.10.0")
        #expect(up.hasUnfinishedSwitch(state: state))                     // Claude Code still on the old plugin
        try writeRegistry(l, marketplacePath: nil, pluginVersion: "0.11.0")
        #expect(!up.hasUnfinishedSwitch(state: state))                    // finished
        #expect(!up.hasUnfinishedSwitch(state: .managed(install("0.11.0", l: l), vaultBootstrapped: false)))
        #expect(!up.hasUnfinishedSwitch(state: .external(install("0.10.0", l: l), .devCheckout)))
    }

    /// Ruling 69 I2: GC never deletes the engine Claude Code's marketplace
    /// actually points at — e.g. a realpath-recorded `engine/0.9.0`.
    @Test func garbageCollectProtectsTheMarketplacesRecordedVersion() throws {
        let l = try layout()
        defer { try? fm.removeItem(at: l.home) }
        for v in ["0.8.0", "0.9.0", "0.10.0", "0.11.0"] {
            try fm.createDirectory(at: l.engineRoot(version: v), withIntermediateDirectories: true)
            try fm.createDirectory(at: l.venv(version: v), withIntermediateDirectories: true)
        }
        try fm.createSymbolicLink(at: l.currentEngineLink, withDestinationURL: l.engineRoot(version: "0.11.0"))
        try writeRegistry(l, marketplacePath: l.engineRoot(version: "0.9.0").path, pluginVersion: nil)
        let removed = try EngineUpgrader(layout: l, release: release("0.11.0")).garbageCollect(keeping: "0.11.0")
        #expect(removed == ["0.8.0"])
        #expect(fm.fileExists(atPath: l.engineRoot(version: "0.9.0").path))
        #expect(fm.fileExists(atPath: l.engineRoot(version: "0.10.0").path))
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
