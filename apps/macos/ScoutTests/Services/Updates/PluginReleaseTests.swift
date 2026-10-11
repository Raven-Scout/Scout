import Testing
import Foundation
@testable import Scout

@Suite("PluginRelease")
struct PluginReleaseTests {
    private func fixture(_ name: String) throws -> Data {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: name, withExtension: "json"),
                               "fixture \(name).json missing from the test bundle")
        return try Data(contentsOf: url)
    }

    // MARK: scoutVersion(fromMarketplaceManifest:)

    @Test func readsTheScoutVersionSkippingOtherPlugins() throws {
        #expect(PluginRelease.scoutVersion(fromMarketplaceManifest: try fixture("updates-marketplace-manifest")) == "0.15.0")
    }

    @Test func nilWhenNoScoutEntry() {
        let data = Data(#"{"plugins":[{"name":"other","version":"1.2.3"}]}"#.utf8)
        #expect(PluginRelease.scoutVersion(fromMarketplaceManifest: data) == nil)
    }

    @Test func nilForAnEmptyVersion() {
        let data = Data(#"{"plugins":[{"name":"scout","version":""}]}"#.utf8)
        #expect(PluginRelease.scoutVersion(fromMarketplaceManifest: data) == nil)
    }

    @Test func nilWhenPluginsIsNotAnArray() {
        let data = Data(#"{"plugins":{"scout":{"version":"0.15.0"}}}"#.utf8)
        #expect(PluginRelease.scoutVersion(fromMarketplaceManifest: data) == nil)
    }

    @Test func nilForNonJSON() {
        #expect(PluginRelease.scoutVersion(fromMarketplaceManifest: Data("not json".utf8)) == nil)
    }

    // MARK: latestManifestURL(for:)

    @Test func latestManifestURLForGitHubSource() {
        #expect(PluginRelease.latestManifestURL(for: .github(repo: "example-org/scout"))?.absoluteString
                == "https://raw.githubusercontent.com/example-org/scout/HEAD/.claude-plugin/marketplace.json")
    }

    @Test func latestManifestURLForGitHubsGitURL() {
        #expect(PluginRelease.latestManifestURL(for: .git(url: "https://github.com/example-org/scout.git"))?.absoluteString
                == "https://raw.githubusercontent.com/example-org/scout/HEAD/.claude-plugin/marketplace.json")
    }

    @Test func latestManifestURLForGitHubsSSHURL() {
        #expect(PluginRelease.latestManifestURL(for: .git(url: "git@github.com:example-org/scout.git"))?.absoluteString
                == "https://raw.githubusercontent.com/example-org/scout/HEAD/.claude-plugin/marketplace.json")
    }

    @Test func latestManifestURLForDirectorySource() {
        #expect(PluginRelease.latestManifestURL(for: .directory(path: "/Users/alex/scout-plugin"))
                == URL(fileURLWithPath: "/Users/alex/scout-plugin/.claude-plugin/marketplace.json"))
    }

    @Test func latestManifestURLIsNilForNonGitHubGitRemote() {
        #expect(PluginRelease.latestManifestURL(for: .git(url: "https://gitlab.example.com/example-org/scout.git")) == nil)
    }

    @Test func latestManifestURLIsNilForOtherSource() {
        #expect(PluginRelease.latestManifestURL(for: .other("npm")) == nil)
    }

    // MARK: releasesURL(for:)

    @Test func releasesURLForGitHubSource() {
        #expect(PluginRelease.releasesURL(for: .github(repo: "example-org/scout"))?.absoluteString
                == "https://github.com/example-org/scout/releases")
    }

    @Test func releasesURLForGitHubsGitURL() {
        #expect(PluginRelease.releasesURL(for: .git(url: "https://github.com/example-org/scout.git"))?.absoluteString
                == "https://github.com/example-org/scout/releases")
    }

    @Test func releasesURLForGitHubsSSHURL() {
        #expect(PluginRelease.releasesURL(for: .git(url: "git@github.com:example-org/scout.git"))?.absoluteString
                == "https://github.com/example-org/scout/releases")
    }

    @Test func releasesURLIsNilForDirectorySource() {
        #expect(PluginRelease.releasesURL(for: .directory(path: "/Users/alex/scout-plugin")) == nil)
    }

    @Test func releasesURLIsNilForNonGitHubGitRemote() {
        #expect(PluginRelease.releasesURL(for: .git(url: "https://gitlab.example.com/example-org/scout.git")) == nil)
    }

    @Test func releasesURLIsNilForOtherSource() {
        #expect(PluginRelease.releasesURL(for: .other("npm")) == nil)
    }

    // MARK: githubRepo(fromGitURL:)

    @Test func githubRepoFromHTTPSURL() {
        #expect(PluginRelease.githubRepo(fromGitURL: "https://github.com/example-org/scout") == "example-org/scout")
    }

    @Test func githubRepoFromHTTPSGitSuffixedURL() {
        #expect(PluginRelease.githubRepo(fromGitURL: "https://github.com/example-org/scout.git") == "example-org/scout")
    }

    @Test func githubRepoFromSSHURL() {
        #expect(PluginRelease.githubRepo(fromGitURL: "git@github.com:example-org/scout.git") == "example-org/scout")
    }

    @Test func githubRepoIsNilForNonGitHubHost() {
        #expect(PluginRelease.githubRepo(fromGitURL: "https://gitlab.example.com/example-org/scout.git") == nil)
    }

    @Test func githubRepoIsNilForMalformedPath() {
        #expect(PluginRelease.githubRepo(fromGitURL: "https://github.com/example-org") == nil)
    }
}
