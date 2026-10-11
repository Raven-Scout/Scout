import Testing
import Foundation
@testable import Scout

@Suite("Topic board — session references from markdown")
struct SessionReferenceTests {
    @Test func claudeLinksOpen() {
        let raw = "https://claude.ai/code/session_0123"
        #expect(SessionReference(raw) == .cloud(URL(string: raw)!))
    }

    @Test func uuidsResume() {
        let id = "0f8fad5b-d9cb-469f-a165-70867728950e"
        #expect(SessionReference(" \(id) ") == .local(id: id))
    }

    @Test(arguments: [
        "--dangerously-skip-permissions",
        "-p hello",
        "https://evil.example/claude.ai",
        "http://claude.ai/code/session_0123",
        "0f8fad5b-d9cb-469f-a165-70867728950e; rm -rf ~",
        "",
    ])
    func everythingElseIsInert(raw: String) {
        #expect(!SessionReference(raw).isActionable)
    }
}
