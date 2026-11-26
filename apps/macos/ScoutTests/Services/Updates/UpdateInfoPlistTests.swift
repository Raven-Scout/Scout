import Testing
import Foundation
@testable import Scout

/// The test host is Scout.app, so `Bundle.main` is the app bundle. These pin
/// the Sparkle contract: drop the key, retype a boolean as the string "YES",
/// or edit the feed URL and this suite goes red.
@Suite("Sparkle Info.plist contract")
struct UpdateInfoPlistTests {
    private var info: [String: Any] { Bundle.main.infoDictionary ?? [:] }

    @Test func feedURLIsTheLatestReleaseAsset() {
        #expect(info["SUFeedURL"] as? String
            == "https://github.com/Raven-Scout/Scout/releases/latest/download/appcast.xml")
    }

    @Test func publicKeyIsA32ByteEd25519Key() throws {
        let key = try #require(info["SUPublicEDKey"] as? String)
        let bytes = try #require(Data(base64Encoded: key))
        #expect(bytes.count == 32)
    }

    @Test func automaticChecksDefaultOn() {
        // `as? Bool` succeeds for a plist <true/> and fails for the string "YES".
        #expect(info["SUEnableAutomaticChecks"] as? Bool == true)
    }

    @Test func checkIntervalIsHourly() {
        // A release used to go unnoticed for up to a day. 3600 is the
        // shortest interval Sparkle accepts in a Release build
        // (`SUMinimumUpdateCheckInterval`); the feed is one small GitHub
        // asset, so an hourly fetch costs nothing.
        #expect((info["SUScheduledCheckInterval"] as? NSNumber)?.intValue == 3600)
    }

    @Test func noSilentAutoDownload() {
        // The approved design shows Sparkle's dialog and lets the user click
        // Install; background auto-download is explicitly not enabled.
        #expect(info["SUAutomaticallyUpdate"] == nil)
    }

    @Test func pluginFloorStampSurvives() {
        // The Sparkle keys share this plist with Part B's floor stamp.
        #expect(info.keys.contains(EngineVersion.floorInfoKey))
    }
}
