import Testing
import Foundation
@testable import Scout

/// SemVer 2.0.0 §11 pre-release precedence. `isBehindBundled` (and so
/// `canUpdate`/`showsHandOff` in `EngineSettingsModel`) depends on this
/// ordering being numeric, not lexicographic — `"rc.9" < "rc.10"` is false
/// as strings but must be true as versions.
@Suite("EngineVersion")
struct EngineVersionTests {
    @Test func numericPreReleaseIdentifiersCompareNumerically() {
        #expect(EngineVersion("1.0.0-rc.9")! < EngineVersion("1.0.0-rc.10")!)
    }

    @Test func shorterIdentifierListSortsFirstWhenAllSharedPartsAreEqual() {
        #expect(EngineVersion("1.0.0-alpha")! < EngineVersion("1.0.0-alpha.1")!)
    }

    @Test func numericIdentifierSortsBeforeAlphanumeric() {
        #expect(EngineVersion("1.0.0-alpha.1")! < EngineVersion("1.0.0-alpha.beta")!)
    }

    @Test func alphanumericIdentifiersCompareInASCIIOrder() {
        #expect(EngineVersion("1.0.0-beta.11")! < EngineVersion("1.0.0-rc.1")!)
    }

    @Test func aReleaseSortsAfterAnyPreReleaseOfTheSameCore() {
        #expect(EngineVersion("1.0.0-rc.1")! < EngineVersion("1.0.0")!)
    }

    @Test func identicalPreReleasesAreEqualNotOrdered() {
        let a = EngineVersion("1.0.0-rc.1")!
        let b = EngineVersion("1.0.0-rc.1")!
        #expect(a == b)
        #expect(!(a < b))
        #expect(!(b < a))
    }

    // MARK: ported from the migration's PluginVersionTests

    @Test func coreComparesNumerically() {
        // The bug a string compare would introduce: "0.10.0" < "0.9.0".
        #expect(EngineVersion("0.10.0")! > EngineVersion("0.9.0")!)
        #expect(EngineVersion("0.9.0")! < EngineVersion("0.10.0")!)
        #expect(EngineVersion("0.9.0")! == EngineVersion("0.9.0")!)
        #expect(EngineVersion("1.0.0")! > EngineVersion("0.99.99")!)
    }

    @Test func shortCoreReadsMissingPartsAsZero() {
        #expect(EngineVersion("1.2") == EngineVersion("1.2.0"))
        #expect(EngineVersion("1") == EngineVersion("1.0.0"))
        #expect(EngineVersion("1.2")?.description == "1.2.0")
    }

    @Test func garbageIsNotAVersion() {
        for text in ["garbage", "", "1.x.0", "1..2", "1.2.3.4", "1.2.3."] {
            #expect(EngineVersion(text) == nil, "\(text)")
        }
    }

    @Test func satisfiedWhenInstalledMeetsOrExceedsFloor() {
        #expect(EngineVersion.isSatisfied(installed: "0.10.0", floor: "0.10.0"))
        #expect(EngineVersion.isSatisfied(installed: "0.11.0", floor: "0.10.0"))
        #expect(!EngineVersion.isSatisfied(installed: "0.9.0", floor: "0.10.0"))
    }

    @Test func installedBehindTheFloorIsDetected() {
        // An installed 0.8.0 against a 0.9.0 build: exactly the skew the
        // floor exists to surface.
        #expect(!EngineVersion.isSatisfied(installed: "0.8.0", floor: "0.9.0"))
    }

    /// Why EngineVersion was kept over PluginVersion: a dot-split numeric
    /// compare read "0.12.0-rc.1" as 0.12.0.1 and called it newer than the
    /// 0.12.0 floor.
    @Test func aPreReleaseOfTheFloorDoesNotMeetIt() {
        #expect(!EngineVersion.isSatisfied(installed: "0.12.0-rc.1", floor: "0.12.0"))
        #expect(EngineVersion.isSatisfied(installed: "0.12.0", floor: "0.12.0-rc.1"))
    }

    @Test func unknownOrUnparseableInstalledVersionIsNotSatisfied() {
        #expect(!EngineVersion.isSatisfied(installed: nil, floor: "0.10.0"))
        #expect(!EngineVersion.isSatisfied(installed: "garbage", floor: "0.0.1"))
    }

    @Test func absentOrUnparseableFloorIsAlwaysSatisfied() {
        // A dev build with no stamped floor must not nag.
        #expect(EngineVersion.isSatisfied(installed: "0.1.0", floor: nil))
        #expect(EngineVersion.isSatisfied(installed: nil, floor: nil))
        #expect(EngineVersion.isSatisfied(installed: "0.1.0", floor: "garbage"))
    }

    @Test func requiredFloorTreatsBlankAndPlaceholderAsUnstamped() {
        #expect(EngineVersion.requiredFloor(stamped: nil) == nil)
        #expect(EngineVersion.requiredFloor(stamped: "") == nil)
        #expect(EngineVersion.requiredFloor(stamped: "$(SCOUT_PLUGIN_FLOOR)") == nil)
        #expect(EngineVersion.requiredFloor(stamped: 12) == nil)
        #expect(EngineVersion.requiredFloor(stamped: "0.12.0") == "0.12.0")
    }

    /// The Info.plist plumbing is intact: the app bundle carries the
    /// `SCScoutPluginFloor` key, and this unstamped build expands it to "".
    @Test func appBundleCarriesAnUnstampedFloorKey() {
        let value = Bundle(for: AppState.self).object(forInfoDictionaryKey: EngineVersion.floorInfoKey)
        #expect(value as? String == "")
        #expect(EngineVersion.requiredFloor(stamped: value) == nil)
    }

    /// `EngineRelease`/tag strings are `v`-prefixed; `EngineUpgrader.needsUpgrade`
    /// compares a bare installed version against a possibly `v`-prefixed one.
    @Test func aLeadingVPrefixParsesAndComparesEqualToTheBareVersion() {
        #expect(EngineVersion("v0.10.0") == EngineVersion("0.10.0"))
    }

    /// Non-SemVer-shaped strings fail to parse instead of crashing or
    /// silently truncating — `EngineUpgrader` relies on this `nil` to treat
    /// an unparsable manifest version as untrusted (Ruling 58). A short core
    /// (`"1.2"`) is NOT malformed: it reads as `1.2.0` (see above).
    @Test func malformedStringsFailToParse() {
        #expect(EngineVersion("nope") == nil)
    }

    /// Ruling 58b: the review found that an `EngineVersion("1.0.0-../../..")`-shaped
    /// string still parsed under the first Ruling 58 fix — `preRelease` had no
    /// charset validation, so a `/` or an empty (`..`) identifier sailed
    /// through. SemVer §9 restricts a pre-release identifier to
    /// `[0-9A-Za-z-]`, non-empty, which closes exactly this gap: a value that
    /// later becomes a single filesystem path component
    /// (`EngineLayout.engineRoot(version:)`) can never carry a path separator
    /// or a traversal segment.
    @Test func preReleaseIdentifiersRejectPathTraversalAndEmptySegments() {
        #expect(EngineVersion("1.0.0-../../evil") == nil)
        #expect(EngineVersion("1.0.0-a/b") == nil)
        #expect(EngineVersion("1.0.0-") == nil)
        #expect(EngineVersion("1.0.0-a..b") == nil)
    }

    /// The charset fix must not regress ordinary, previously-accepted
    /// pre-release shapes: dotted numeric/alpha identifiers and a hyphen
    /// *within* a single identifier (hyphen is a legal SemVer identifier
    /// character, not just the pre-release delimiter).
    @Test func ordinaryPreReleaseShapesStillParse() {
        #expect(EngineVersion("1.0.0-rc.1") != nil)
        #expect(EngineVersion("1.0.0-alpha-2") != nil)
    }

    /// SemVer §9: a purely-numeric identifier longer than one digit may not
    /// have a leading zero (`"01"` is invalid; `"0"` alone is fine). No
    /// existing case exercised this, so adding the restriction doesn't
    /// regress anything (Ruling 58b).
    @Test func leadingZeroNumericPreReleaseIdentifiersAreRejected() {
        #expect(EngineVersion("1.0.0-01") == nil)
        #expect(EngineVersion("1.0.0-rc.01") == nil)
        #expect(EngineVersion("1.0.0-0") != nil)
    }

    /// The §9 charset rules sit beside Part B's lenient short core: a short
    /// core with a valid pre-release still parses, an invalid one does not.
    @Test func shortCoreAndPreReleaseRulesCompose() {
        #expect(EngineVersion("1.2-rc.1") == EngineVersion("1.2.0-rc.1"))
        #expect(EngineVersion("1.2-a/b") == nil)
    }
}
