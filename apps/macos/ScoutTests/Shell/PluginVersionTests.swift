import Testing
import Foundation
@testable import Scout

@Suite("plugin version floor")
struct PluginVersionTests {
    @Test func comparesSemverNumerically() {
        // The bug a string compare would introduce: "0.10.0" < "0.9.0".
        #expect(PluginVersion.compare("0.10.0", "0.9.0") == .orderedDescending)
        #expect(PluginVersion.compare("0.9.0", "0.10.0") == .orderedAscending)
        #expect(PluginVersion.compare("0.9.0", "0.9.0") == .orderedSame)
        #expect(PluginVersion.compare("1.0.0", "0.99.99") == .orderedDescending)
    }

    @Test func satisfiedWhenInstalledMeetsOrExceedsFloor() {
        #expect(PluginVersion.isSatisfied(installed: "0.10.0", floor: "0.10.0"))
        #expect(PluginVersion.isSatisfied(installed: "0.11.0", floor: "0.10.0"))
        #expect(!PluginVersion.isSatisfied(installed: "0.9.0", floor: "0.10.0"))
    }

    @Test func liveSkewOnThisMachineIsDetected() {
        // Installed 0.8.0 against a 0.9.0 repo — the real state when this
        // was written, and exactly what the floor exists to surface.
        #expect(!PluginVersion.isSatisfied(installed: "0.8.0", floor: "0.9.0"))
    }

    @Test func unknownInstalledVersionIsNotSatisfied() {
        #expect(!PluginVersion.isSatisfied(installed: nil, floor: "0.10.0"))
    }

    @Test func absentFloorIsAlwaysSatisfied() {
        // A dev build with no stamped floor must not nag.
        #expect(PluginVersion.isSatisfied(installed: "0.1.0", floor: nil))
        #expect(PluginVersion.isSatisfied(installed: nil, floor: nil))
    }

    @Test func toleratesShortAndDirtyVersionStrings() {
        #expect(PluginVersion.compare("1.2", "1.2.0") == .orderedSame)
        #expect(PluginVersion.compare("garbage", "0.0.1") == .orderedAscending)
    }
}
