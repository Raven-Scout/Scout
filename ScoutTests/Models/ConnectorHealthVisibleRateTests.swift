import Foundation
import Testing
@testable import Scout

/// CC-7: the rail card's percentage must match the cells the user can see —
/// `.absent` sessions are neutral (excluded from both sides of the ratio),
/// never counted as failures.
@Suite("ConnectorHealthMatrix.visibleHealthRate")
struct ConnectorHealthVisibleRateTests {

    private func call(
        session: String, connector: String, error: Bool, minute: Int
    ) -> ConnectorCall {
        ConnectorCall(
            ts: Date(timeIntervalSince1970: 1_781_600_000 + TimeInterval(minute * 60)),
            sessionId: session, mode: "briefing", tool: "search",
            connector: connector, error: error, err: error ? "boom" : nil)
    }

    // MARK: - the CC-7 worked example

    @Test("3 ok / 1 error / 1 absent reads as 75%, not 60% and not 100%")
    func visibleHealthRate_absentSessionsAreNeutral() {
        let calls = [
            call(session: "s1", connector: "slack", error: false, minute: 1),
            call(session: "s2", connector: "slack", error: false, minute: 2),
            call(session: "s3", connector: "slack", error: false, minute: 3),
            call(session: "s4", connector: "slack", error: true,  minute: 4),
            // s5 exists (another connector was called) but slack was not.
            call(session: "s5", connector: "gmail", error: false, minute: 5),
        ]
        let matrix = ConnectorHealthMatrix(calls: calls, connectors: ["slack", "gmail"])
        let rate = matrix.visibleHealthRate(connector: "slack", in: matrix.sessionsNewestFirst)
        #expect(rate == 0.75)               // 3 ok of 4 *called*; s5 excluded
    }

    @Test("a connector absent from every visible session yields nil, not zero")
    func visibleHealthRate_neverCalledIsNil() {
        let calls = [call(session: "s1", connector: "gmail", error: false, minute: 1)]
        let matrix = ConnectorHealthMatrix(calls: calls, connectors: ["gmail", "slack"])
        #expect(matrix.visibleHealthRate(connector: "slack", in: matrix.sessionsNewestFirst) == nil)
    }

    @Test("an unknown connector yields nil")
    func visibleHealthRate_unknownConnectorIsNil() {
        let calls = [call(session: "s1", connector: "gmail", error: false, minute: 1)]
        let matrix = ConnectorHealthMatrix(calls: calls, connectors: ["gmail"])
        #expect(matrix.visibleHealthRate(connector: "nope", in: matrix.sessionsNewestFirst) == nil)
    }

    @Test("an empty visible window yields nil")
    func visibleHealthRate_emptyWindowIsNil() {
        let calls = [call(session: "s1", connector: "gmail", error: false, minute: 1)]
        let matrix = ConnectorHealthMatrix(calls: calls, connectors: ["gmail"])
        #expect(matrix.visibleHealthRate(connector: "gmail", in: []) == nil)
    }

    @Test("a partial session counts as called but not ok")
    func visibleHealthRate_partialCountsAsCalledNotOk() {
        let calls = [
            call(session: "s1", connector: "slack", error: false, minute: 1),
            call(session: "s1", connector: "slack", error: true,  minute: 1),
            call(session: "s2", connector: "slack", error: false, minute: 2),
        ]
        let matrix = ConnectorHealthMatrix(calls: calls, connectors: ["slack"])
        #expect(matrix.cell(connector: "slack", sessionId: "s1") == .partial(ok: 1, total: 2))
        // s1 partial (called, not ok) + s2 ok → 1/2
        #expect(matrix.visibleHealthRate(connector: "slack", in: matrix.sessionsNewestFirst) == 0.5)
    }

    @Test("all-ok is 1.0 and all-error is 0.0")
    func visibleHealthRate_extremes() {
        let allOK = ConnectorHealthMatrix(
            calls: [call(session: "s1", connector: "c", error: false, minute: 1),
                    call(session: "s2", connector: "c", error: false, minute: 2)],
            connectors: ["c"])
        #expect(allOK.visibleHealthRate(connector: "c", in: allOK.sessionsNewestFirst) == 1.0)

        let allErr = ConnectorHealthMatrix(
            calls: [call(session: "s1", connector: "c", error: true, minute: 1),
                    call(session: "s2", connector: "c", error: true, minute: 2)],
            connectors: ["c"])
        #expect(allErr.visibleHealthRate(connector: "c", in: allErr.sessionsNewestFirst) == 0.0)
    }

    @Test("only the sessions passed in are considered")
    func visibleHealthRate_respectsTheVisibleSlice() {
        let calls = [
            call(session: "s1", connector: "c", error: true,  minute: 1),   // oldest
            call(session: "s2", connector: "c", error: false, minute: 2),
            call(session: "s3", connector: "c", error: false, minute: 3),   // newest
        ]
        let matrix = ConnectorHealthMatrix(calls: calls, connectors: ["c"])
        // Newest two only — the old failure is out of the window.
        let visible = Array(matrix.sessionsNewestFirst.prefix(2))
        #expect(matrix.visibleHealthRate(connector: "c", in: visible) == 1.0)
        // Whole window includes it.
        #expect(matrix.visibleHealthRate(connector: "c", in: matrix.sessionsNewestFirst)
                == 2.0 / 3.0)
    }

    // MARK: - construction

    @Test("sessions are ordered newest first by their earliest call")
    func sessions_orderedNewestFirst() {
        let calls = [
            call(session: "old", connector: "c", error: false, minute: 1),
            call(session: "new", connector: "c", error: false, minute: 9),
            call(session: "mid", connector: "c", error: false, minute: 5),
        ]
        let matrix = ConnectorHealthMatrix(calls: calls, connectors: ["c"])
        #expect(matrix.sessionsNewestFirst.map(\.id) == ["new", "mid", "old"])
        #expect(matrix.sessionsNewestFirst.first?.mode == "briefing")
    }

    @Test("no calls at all produces an empty matrix")
    func emptyCalls_producesEmptyMatrix() {
        let matrix = ConnectorHealthMatrix(calls: [], connectors: ["slack"])
        #expect(matrix.sessionsNewestFirst.isEmpty)
        #expect(matrix.connectors == ["slack"])
        #expect(matrix.cell(connector: "slack", sessionId: "anything") == .absent)
        #expect(matrix.successRate(connector: "slack") == 0.0)
        #expect(matrix.visibleHealthRate(connector: "slack", in: []) == nil)
    }

    @Test("successRate is 0 for a connector that was never called")
    func successRate_neverCalledIsZero() {
        let calls = [call(session: "s1", connector: "gmail", error: false, minute: 1)]
        let matrix = ConnectorHealthMatrix(calls: calls, connectors: ["gmail", "slack"])
        #expect(matrix.successRate(connector: "slack") == 0.0)
        #expect(matrix.successRate(connector: "gmail") == 1.0)
    }
}
