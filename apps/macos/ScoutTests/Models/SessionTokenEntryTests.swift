import Testing
import Foundation
@testable import Scout

@Suite("SessionTokenEntry")
struct SessionTokenEntryTests {
    @Test func decodesSuccessRow() throws {
        let json = #"""
        {"ts":"2026-04-22T22:10:33Z","ts_et":"2026-04-22 18:10 EDT","session_id":"abc","scout_mode":"dreaming","cwd":"/x","primary_model":"claude-opus-4-7","input_tokens":1200,"output_tokens":340,"cache_read_input_tokens":89000,"cache_creation_input_tokens":9200,"cost_usd":0.412,"num_turns":37,"duration_ms":512000,"error":null}
        """#
        let decoder = SessionTokenEntry.makeDecoder()
        let entry = try decoder.decode(SessionTokenEntry.self, from: Data(json.utf8))
        #expect(entry.sessionId == "abc")
        #expect(entry.scoutMode == "dreaming")
        #expect(entry.primaryModel == "claude-opus-4-7")
        #expect(entry.inputTokens == 1200)
        #expect(entry.costUSD == Decimal(string: "0.412"))
        #expect(entry.error == nil)
    }

    @Test func decodesErrorRow() throws {
        let json = #"""
        {"ts":"2026-04-22T23:30:00Z","ts_et":"2026-04-22 19:30 EDT","session_id":"ghi","scout_mode":"manual","cwd":"/x","primary_model":null,"input_tokens":0,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"cost_usd":0,"num_turns":0,"duration_ms":0,"error":"transcript_not_found"}
        """#
        let decoder = SessionTokenEntry.makeDecoder()
        let entry = try decoder.decode(SessionTokenEntry.self, from: Data(json.utf8))
        #expect(entry.primaryModel == nil)
        #expect(entry.error == "transcript_not_found")
    }
}
