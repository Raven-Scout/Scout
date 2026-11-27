import Foundation
import Testing
@testable import Scout

/// Layout v2 (ADR 0011): every vault path the app hardcodes (`VaultLayout`) equals what the engine publishes in
/// `scoutctl vault layout --json`. The app doesn't call the command yet; this keeps its constants from drifting.
///
/// `vault-layout.json` is a byte copy of the engine's generated
/// `plugin/engine/tests/fixtures/contract/vault-layout.json` (root CLAUDE.md). When it is regenerated, check the
/// pins below still hold.
@Suite("Vault layout contract")
struct VaultLayoutContractTests {
    private final class FixtureAnchor {}

    private static func fixture() throws -> [String: Any] {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: "vault-layout", withExtension: "json"))
        let object = try JSONSerialization.jsonObject(with: Data(contentsOf: url))
        return try #require(object as? [String: Any])
    }

    private static func paths() throws -> [String: String] {
        try #require(try fixture()["paths"] as? [String: String])
    }

    @Test func theFixtureIsLayoutVersionTwo() throws {
        let doc = try Self.fixture()
        #expect(doc["schema_version"] as? Int == 1)
        #expect(doc["layout_version"] as? Int == 2)
    }

    @Test func everyHardcodedPathMatchesTheEngine() throws {
        let p = try Self.paths()
        #expect(p["brain_dir"] == VaultLayout.brainDir)
        #expect(p["runners_dir"] == VaultLayout.runnersDir)
        #expect(p["scripts_dir"] == VaultLayout.scriptsDir)
        #expect(p["hooks_dir"] == VaultLayout.hooksDir)
        #expect(p["records_dir"] == VaultLayout.recordsDir)
        #expect(p["dreaming_proposals_dir"] == VaultLayout.dreamingProposalsDir)
        #expect(p["wishlist_dir"] == VaultLayout.wishlistDir)
        #expect(p["connector_health"] == VaultLayout.connectorHealth)
        #expect(p["session_log"] == VaultLayout.sessionLog)
        #expect(p["mistake_audit"] == VaultLayout.mistakeAudit)
        #expect(p["knowledge_base"] == VaultLayout.knowledgeBase)
        #expect(p["research_queue_dir"] == VaultLayout.researchQueueDir)
        #expect(p["meetings_registry"] == VaultLayout.meetingsRegistry)
        #expect(p["action_items_dir"] == VaultLayout.actionItemsDir)
        #expect(p["context_notes_dir"] == VaultLayout.contextNotesDir)
        #expect(p["reports_dir"] == VaultLayout.reportsDir)
        #expect(p["inbox"] == VaultLayout.inbox)
    }

    @Test func theDefaultSpaceAndItsProjectsIndexMatchTheEngine() throws {
        let doc = try Self.fixture()
        let spaces = try #require(doc["spaces"] as? [[String: Any]])
        let def = try #require(spaces.first { $0["default"] as? Bool == true })
        #expect(def["key"] as? String == VaultLayout.defaultSpace)
        let spacePaths = try #require(doc["space_paths"] as? [String: String])
        let projects = try #require(spacePaths["projects"]).replacingOccurrences(of: "{space}", with: VaultLayout.defaultSpace)
        #expect(VaultLayout.defaultProjectsIndex == projects + "/projects.md")
    }

    @Test func theFolderSettingsDefaultToTheLayout() {
        #expect(PerFileTabConfig.wishlist.directoryDefaultRelative == VaultLayout.wishlistDir)
        #expect(PerFileTabConfig.research.directoryDefaultRelative == VaultLayout.researchQueueDir)
    }

    @Test func anOverrideNamingTheV1DefaultIsCleared() throws {
        let suite = "VaultLayoutContractTests-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let scoutDir = URL(fileURLWithPath: "/tmp/ScoutVault")
        defaults.set("/tmp/ScoutVault/dreaming-proposals", forKey: "dreamingProposalsPath")
        defaults.set("docs/wishlist", forKey: "wishlistPath")
        defaults.set("/Users/alex/elsewhere", forKey: "researchQueuePath")
        VaultLayout.clearV1DefaultOverrides(defaults, scoutDir: scoutDir)
        #expect(defaults.string(forKey: "dreamingProposalsPath") == nil)
        #expect(defaults.string(forKey: "wishlistPath") == nil)
        #expect(defaults.string(forKey: "researchQueuePath") == "/Users/alex/elsewhere")
    }
}
