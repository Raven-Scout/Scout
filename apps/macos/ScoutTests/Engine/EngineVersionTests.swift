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
}
