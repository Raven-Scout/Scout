import Foundation

/// `major.minor.patch[-pre]`; pre-release sorts before the release. #74's
/// `SemVer` covers the same ground — dedupe onto one type when both exist.
///
/// This is the app's one engine/plugin version type. The monorepo
/// migration's `PluginVersion` folded into it: its floor plumbing
/// (`floorInfoKey`, `requiredFloor`, `isSatisfied(installed:floor:)`) lives
/// here, and so does its tolerance for a short core (`1.2` reads as
/// `1.2.0`).
nonisolated struct EngineVersion: Equatable, Comparable, Sendable, CustomStringConvertible {
    let major: Int, minor: Int, patch: Int
    let preRelease: String?

    /// Missing `minor`/`patch` read as 0. A core part that is not an integer,
    /// or a core longer than three parts, is not a version. (The core never
    /// holds a `-`: the first one starts the pre-release.)
    init?(_ text: String) {
        let trimmed = text.hasPrefix("v") ? String(text.dropFirst()) : text
        let core = trimmed.split(separator: "-", maxSplits: 1, omittingEmptySubsequences: false)
        let parts = core[0].split(separator: ".", omittingEmptySubsequences: false).map { Int($0) }
        guard (1...3).contains(parts.count), parts.allSatisfy({ $0 != nil }) else { return nil }
        let numbers = parts.compactMap { $0 } + Array(repeating: 0, count: 3 - parts.count)
        major = numbers[0]; minor = numbers[1]; patch = numbers[2]
        preRelease = core.count == 2 && !core[1].isEmpty ? String(core[1]) : nil
    }

    // MARK: the app↔engine floor

    /// Info.plist key `scripts/release.sh` stamps with the plugin version
    /// (equal to the app's own version) in the same tree the binary was
    /// built from (`plugin/.claude-plugin/plugin.json`). Dev builds leave the
    /// `SCOUT_PLUGIN_FLOOR` build setting empty. Nothing in the UI reads the
    /// floor yet.
    static let floorInfoKey = "SCScoutPluginFloor"

    /// Minimum engine version this build requires; nil in unstamped dev builds.
    static var requiredFloor: String? {
        requiredFloor(stamped: Bundle.main.object(forInfoDictionaryKey: floorInfoKey))
    }

    /// The Info.plist value as a floor: an empty string or the unexpanded
    /// `$(SCOUT_PLUGIN_FLOOR)` placeholder means "not stamped".
    static func requiredFloor(stamped value: Any?) -> String? {
        guard let v = value as? String, !v.isEmpty, v != "$(SCOUT_PLUGIN_FLOOR)" else { return nil }
        return v
    }

    /// True when `installed` meets or exceeds `floor`, in SemVer order (a
    /// pre-release of the floor does not meet it). An absent floor is always
    /// satisfied, so dev builds do not nag, and so is one that does not parse,
    /// since it cannot be enforced. An absent or unparseable installed version
    /// never satisfies a real floor: we could not tell what is installed,
    /// and that is the problem to surface.
    static func isSatisfied(installed: String?, floor: String?) -> Bool {
        guard let floor, let required = EngineVersion(floor) else { return true }
        guard let installed, let have = EngineVersion(installed) else { return false }
        return have >= required
    }

    static func < (l: EngineVersion, r: EngineVersion) -> Bool {
        if (l.major, l.minor, l.patch) != (r.major, r.minor, r.patch) { return (l.major, l.minor, l.patch) < (r.major, r.minor, r.patch) }
        switch (l.preRelease, r.preRelease) {
        case (nil, nil): return false
        case (.some, nil): return true
        case (nil, .some): return false
        case (.some(let a), .some(let b)): return comparePreRelease(a, b) < 0
        }
    }

    /// SemVer 2.0.0 §11: split on `.`, compare identifiers left to right.
    /// Numeric identifiers compare numerically; a numeric identifier sorts
    /// before an alphanumeric one; alphanumeric identifiers compare in ASCII
    /// order; if every shared identifier is equal, the shorter list sorts
    /// first.
    private static func comparePreRelease(_ lhs: String, _ rhs: String) -> Int {
        let lIdentifiers = lhs.split(separator: ".", omittingEmptySubsequences: false)
        let rIdentifiers = rhs.split(separator: ".", omittingEmptySubsequences: false)
        for (a, b) in zip(lIdentifiers, rIdentifiers) {
            if a == b { continue }
            switch (Int(a), Int(b)) {
            case (let x?, let y?): return x < y ? -1 : 1
            case (.some, nil): return -1
            case (nil, .some): return 1
            case (nil, nil): return a < b ? -1 : 1
            }
        }
        if lIdentifiers.count == rIdentifiers.count { return 0 }
        return lIdentifiers.count < rIdentifiers.count ? -1 : 1
    }

    var description: String { "\(major).\(minor).\(patch)" + (preRelease.map { "-\($0)" } ?? "") }
}
