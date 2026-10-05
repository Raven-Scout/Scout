import Foundation

/// The app↔engine version contract.
///
/// Spec §6: the floor is stamped into Info.plist at build time from
/// `plugin/.claude-plugin/plugin.json` in the same tree the binary was built
/// from — a fact about the source, not a hand-maintained Swift literal. This
/// is what the v0.4 unification spec §8 called `CapabilityChecker` and never
/// got, which is why version skew was only ever detected by grepping
/// `scoutctl --help` output.
enum PluginVersion {
    /// Info.plist key written by `scripts/release-app.sh`.
    static let floorInfoKey = "SCScoutPluginFloor"

    /// Minimum plugin version this build requires; nil in unstamped dev builds.
    static var requiredFloor: String? {
        guard let v = Bundle.main.object(forInfoDictionaryKey: floorInfoKey) as? String,
              !v.isEmpty, v != "$(SCOUT_PLUGIN_FLOOR)"
        else { return nil }
        return v
    }

    /// Numeric semver-ish comparison. Missing components read as 0;
    /// non-numeric components read as 0, so garbage sorts below any real
    /// version rather than throwing.
    static func compare(_ lhs: String, _ rhs: String) -> ComparisonResult {
        let l = lhs.split(separator: ".").map { Int($0) ?? 0 }
        let r = rhs.split(separator: ".").map { Int($0) ?? 0 }
        for i in 0..<max(l.count, r.count) {
            let a = i < l.count ? l[i] : 0
            let b = i < r.count ? r[i] : 0
            if a != b { return a < b ? .orderedAscending : .orderedDescending }
        }
        return .orderedSame
    }

    /// True when `installed` meets or exceeds `floor`.
    /// An absent floor is always satisfied (dev builds must not nag).
    /// An absent installed version is never satisfied when a floor exists —
    /// we could not find the plugin, which is itself the problem to surface.
    static func isSatisfied(installed: String?, floor: String?) -> Bool {
        guard let floor else { return true }
        guard let installed else { return false }
        return compare(installed, floor) != .orderedAscending
    }
}
