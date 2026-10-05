import Foundation

/// Decides whether the bundled engine is newer than the managed one and
/// tidies old versions afterwards (spec §4.4). The steps themselves are
/// `EngineInstaller`'s; `bootstrap auto` dispatches to `upgrade`, which
/// re-renders plists and shim to the new venv and rewrites the pointer.
nonisolated struct EngineUpgrader: Sendable {
    let layout: EngineLayout
    let release: EngineRelease

    static let upgradeSteps: [InstallStep] = [.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode, .bootstrapVault, .verify]

    func needsUpgrade(state: EngineState) -> Bool {
        guard case .managed(let install, _) = state,
              let installed = install.version.flatMap(EngineVersion.init),
              let bundled = EngineVersion(release.engine.version) else { return false }
        return installed < bundled
    }

    /// Remove every `engine/<v>` and `venv/<v>` except `current`, the newest
    /// other version (spec §11 Q5: keep one previous), and whatever `current`
    /// actually resolves to on disk right now. That last protection holds
    /// even when `keeping` disagrees with the live symlink — defense in
    /// depth, so a stale or wrong caller argument can never delete the engine
    /// Scout is actually running. Returns the removed versions.
    func garbageCollect(keeping current: String) throws -> [String] {
        let fileManager = FileManager.default
        let entries = (try? fileManager.contentsOfDirectory(atPath: layout.engineDir.path)) ?? []
        // Pair each candidate name with its parsed version up front so the
        // sort never has to re-parse (and never force-unwraps) an entry the
        // filter already guaranteed is valid.
        let candidates: [(name: String, version: EngineVersion)] = entries.compactMap { name in
            guard name != "current", name != "current.tmp", !name.hasSuffix(".partial"),
                  let version = EngineVersion(name) else { return nil }
            return (name, version)
        }
        let versions = candidates.sorted { $0.version < $1.version }.map(\.name)
        let protectedVersions = Set([current, currentLinkedVersion].compactMap { $0 })
        let previous = versions.filter { !protectedVersions.contains($0) }.last
        var removed: [String] = []
        for version in versions where !protectedVersions.contains(version) && version != previous {
            try? fileManager.removeItem(at: layout.engineRoot(version: version))
            try? fileManager.removeItem(at: layout.venv(version: version))
            removed.append(version)
        }
        return removed
    }

    /// The version `current` actually points at right now, read directly off
    /// the symlink rather than trusted from a caller's `keeping` argument.
    /// Only the last path component matters here, so it doesn't need
    /// `EngineLocator.conventionalLayout`'s absolute/relative resolution.
    private var currentLinkedVersion: String? {
        (try? FileManager.default.destinationOfSymbolicLink(atPath: layout.currentEngineLink.path))
            .map { URL(fileURLWithPath: $0).lastPathComponent }
    }
}
