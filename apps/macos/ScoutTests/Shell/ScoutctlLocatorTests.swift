import Testing
import Foundation
@testable import Scout

@Suite("scoutctl resolution")
struct ScoutctlLocatorTests {
    /// Shape of ~/.claude/plugins/installed_plugins.json as of plugin 0.8.0.
    static let installedJSON = """
    {"plugins":{"scout@scout-plugin":[{"scope":"user",\
    "installPath":"/Users/x/.claude/plugins/cache/scout-plugin/scout/0.8.0",\
    "version":"0.8.0","installedAt":"2026-05-09T11:18:42.951Z"}]}}
    """

    @Test func prefersTheInstalledPluginCache() {
        // Both the installed-cache path AND the dev-checkout path report as
        // executable here — the only way this test can tell them apart is
        // priority order. If the dev checkout were ever checked first (or
        // the cache candidate dropped), this must fail even though a
        // "some path exists" version of this test would still pass.
        let home = URL(fileURLWithPath: "/Users/x")
        let expected = "/Users/x/.claude/plugins/cache/scout-plugin/scout/0.8.0/engine/bin/scoutctl"
        let devCheckout = "/Users/x/scout-plugin/engine/bin/scoutctl"
        let result = ScoutctlLocator.resolve(
            home: home,
            installedPluginsJSON: Self.installedJSON,
            isExecutable: { $0.path == expected || $0.path == devCheckout }
        )
        #expect(result.executable.path == expected)
        #expect(result.argsPrefix.isEmpty)
    }

    @Test func fallsBackToDevCheckoutAtEngineBin() {
        // The historical bug: the dev candidate was ~/scout-plugin/bin/scoutctl,
        // which has never existed. It must be engine/bin/scoutctl.
        let home = URL(fileURLWithPath: "/Users/x")
        let dev = "/Users/x/scout-plugin/engine/bin/scoutctl"
        let result = ScoutctlLocator.resolve(
            home: home,
            installedPluginsJSON: nil,
            isExecutable: { $0.path == dev }
        )
        #expect(result.executable.path == dev)
    }

    @Test func neverProposesTheNonexistentBinPath() {
        let home = URL(fileURLWithPath: "/Users/x")
        var probed: [String] = []
        _ = ScoutctlLocator.resolve(
            home: home,
            installedPluginsJSON: nil,
            isExecutable: { probed.append($0.path); return false }
        )
        #expect(!probed.contains("/Users/x/scout-plugin/bin/scoutctl"),
                "the bin/ path never existed; probing it is the bug being fixed")
    }

    @Test func fallsBackToEnvOnPathWhenNothingOnDisk() {
        let result = ScoutctlLocator.resolve(
            home: URL(fileURLWithPath: "/Users/x"),
            installedPluginsJSON: nil,
            isExecutable: { _ in false }
        )
        #expect(result.executable.path == "/usr/bin/env")
        #expect(result.argsPrefix == ["scoutctl"])
    }

    @Test func installedPluginPathParsesTheManifest() {
        #expect(ScoutctlLocator.installedPluginPath(json: Self.installedJSON)
                == "/Users/x/.claude/plugins/cache/scout-plugin/scout/0.8.0")
    }

    @Test func installedPluginPathToleratesGarbage() {
        #expect(ScoutctlLocator.installedPluginPath(json: "not json") == nil)
        #expect(ScoutctlLocator.installedPluginPath(json: #"{"plugins":{}}"#) == nil)
    }
}
