import Testing
import Foundation
@testable import Scout

@Suite("UpdateFeed override (SCOUT_APPCAST_URL)")
struct UpdateFeedTests {
    private func override(_ value: String?) -> String? {
        var env: [String: String] = [:]
        if let value { env[UpdateFeed.overrideEnvironmentKey] = value }
        return UpdateFeed.overrideURLString(environment: env)
    }

    @Test func keyNameIsStable() { #expect(UpdateFeed.overrideEnvironmentKey == "SCOUT_APPCAST_URL") }

    @Test func httpsOverrideIsReturnedVerbatim() {
        let url = "https://github.com/example-org/repo/releases/download/v0.15.1-rc.1/appcast.xml"
        #expect(override(url) == url)
    }

    @Test func missingBlankHTTPAndNonURLsAreRejected() {
        #expect(override(nil) == nil)
        #expect(override("") == nil)
        #expect(override("  \n") == nil)
        #expect(override("http://localhost:8000/appcast.xml") == nil)
        #expect(override("HTTP://example.com/appcast.xml") == nil)
        #expect(override("not a url") == nil)
        #expect(override("https://") == nil)
        #expect(override("file:///tmp/appcast.xml") == nil)
    }

    @Test func surroundingWhitespaceIsTrimmed() {
        #expect(override(" https://example.com/appcast.xml\n") == "https://example.com/appcast.xml")
    }
}
