import Testing
import Foundation
@testable import Scout

@Suite("BootstrapResult")
struct BootstrapResultTests {
    @Test func decodesTheEngineContract() {
        let json = """
        {"schema_version": 1, "action": "install", "reason": "no vault: directory missing or empty", "dry_run": false,
         "vault": "/Users/alex/Scout", "plugin_version": "0.10.0", "error": null,
         "doctor": {"severity": "yellow", "errors": [], "warnings": ["snapshot missing: x"]},
         "conflicts": [], "backups": [], "snapshots_recorded": [], "pointer": "/Users/alex/.local/state/scout/engine.json"}
        """
        let r = BootstrapResult.parse(Data(json.utf8))
        #expect(r?.action == "install")
        #expect(r?.pluginVersion == "0.10.0")
        #expect(r?.doctor?.severity == .yellow)
        #expect(r?.pointer == "/Users/alex/.local/state/scout/engine.json")
    }

    @Test func refusedCarriesError() {
        let r = BootstrapResult.parse(Data(#"{"schema_version":1,"action":"refused","reason":"","dry_run":false,"vault":"/v","plugin_version":"0.10.0","error":"install needs --user-name","doctor":null,"conflicts":[],"backups":[],"snapshots_recorded":[],"pointer":null}"#.utf8))
        #expect(r?.action == "refused" && r?.error == "install needs --user-name" && r?.doctor == nil)
    }

    /// The engine contract grows additively (e.g. `mutated`, `vault_edits`
    /// landed after this decoder shipped); unknown keys must never break
    /// decoding of the fields the app actually reads.
    @Test func ignoresAdditiveContractKeys() {
        let json = """
        {"schema_version": 1, "action": "upgrade", "reason": "", "dry_run": false,
         "vault": "/Users/alex/Scout", "plugin_version": "0.12.0", "error": null,
         "doctor": null, "conflicts": [], "backups": [], "snapshots_recorded": [], "pointer": null,
         "mutated": true, "vault_edits": ["notes/2026-10-04.md"]}
        """
        let r = BootstrapResult.parse(Data(json.utf8))
        #expect(r?.action == "upgrade")
        #expect(r?.pluginVersion == "0.12.0")
        #expect(r?.vault == "/Users/alex/Scout")
    }
}
