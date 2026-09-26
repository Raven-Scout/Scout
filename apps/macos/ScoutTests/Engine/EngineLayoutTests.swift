import Testing
import Foundation
@testable import Scout

@Suite("EngineLayout")
struct EngineLayoutTests {
    let layout = EngineLayout(home: URL(fileURLWithPath: "/Users/alex"))

    @Test func canonicalPathsMatchTheSpec() {
        #expect(layout.engineDir.path == "/Users/alex/.local/share/scout/engine")
        #expect(layout.currentEngineLink.path == "/Users/alex/.local/share/scout/engine/current")
        #expect(layout.venvDir.path == "/Users/alex/.local/share/scout/venv")
        #expect(layout.pointerURL.path == "/Users/alex/.local/state/scout/engine.json")
        #expect(layout.installLogURL.path == "/Users/alex/.local/state/scout/install.log")
        #expect(layout.shimURL.path == "/Users/alex/.local/bin/scoutctl")
        #expect(layout.uvURL.path == "/Users/alex/.local/bin/uv")
        #expect(layout.claudePluginsDir.path == "/Users/alex/.claude/plugins")
        #expect(layout.devCheckout.path == "/Users/alex/scout-plugin")
    }

    @Test func versionedPaths() {
        #expect(layout.engineRoot(version: "0.10.0").path == "/Users/alex/.local/share/scout/engine/0.10.0")
        #expect(layout.venv(version: "0.10.0").path == "/Users/alex/.local/share/scout/venv/0.10.0")
        #expect(layout.scoutctl(version: "0.10.0").path == "/Users/alex/.local/share/scout/venv/0.10.0/bin/scoutctl")
    }
}
