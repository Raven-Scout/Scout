import Foundation
import Testing
@testable import Scout

/// An empty Sessions page says why (UI audit #37), and a card names its
/// state in words with a symbol, not only by its coloured edge (#35).
@Suite("Sessions empty states and state labels")
struct SessionsEmptyAndStateTests {
    private let now = SessionsFixture.now

    private func index(sessions: [[String: Any]]? = nil) throws -> SessionIndex {
        var object = try SessionsFixture.object()
        if let sessions { object["sessions"] = sessions }
        return try SessionIndex.decode(SessionsFixture.encode(object))
    }

    @Test("a first run with no sessions isn't 'no matches'")
    func noSessionsYet() throws {
        let empty = try index(sessions: [])
        #expect(SessionsLayout.emptyReason(index: empty, filter: SessionsFilter(), now: now) == .noSessionsYet)
        var search = SessionsFilter()
        search.search = "anything"
        #expect(SessionsLayout.emptyReason(index: empty, filter: search, now: now) == .noSessionsYet)
        #expect(SessionsEmptyReason.noSessionsYet.title == "No Claude Code sessions yet")
        #expect(!SessionsEmptyReason.noSessionsYet.offersClearFilters)
    }

    @Test("filters that match nothing offer to clear them")
    func noMatches() throws {
        let full = try index()
        #expect(SessionsLayout.emptyReason(index: full, filter: SessionsFilter(), now: now) == nil)
        var search = SessionsFilter()
        search.search = "zzz-no-such-session"
        #expect(SessionsLayout.emptyReason(index: full, filter: search, now: now) == .noMatches)
        var project = SessionsFilter()
        project.projectKey = "/nowhere"
        #expect(SessionsLayout.emptyReason(index: full, filter: project, now: now) == .noMatches)
        #expect(SessionsEmptyReason.noMatches.title == "No sessions match these filters.")
        #expect(SessionsEmptyReason.noMatches.offersClearFilters)
    }

    @Test("sessions the default view hides are named as hidden, not as filtered out")
    func onlyHiddenByDefault() throws {
        let all = try SessionsFixture.object()["sessions"] as? [[String: Any]] ?? []
        // Keep only Scout's own runs and finished sessions.
        let full = try index()
        let hiddenIDs = Set(full.sessions.filter { $0.isScoutRun || $0.state == .done }.map(\.id))
        let hidden = all.filter { hiddenIDs.contains($0["id"] as? String ?? "") }
        try #require(!hidden.isEmpty)
        let onlyHidden = try index(sessions: hidden)
        let hours = onlyHidden.display.doneVisibleHours
        let reason = SessionsLayout.emptyReason(index: onlyHidden, filter: SessionsFilter(), now: now)
        #expect(reason == .allHidden(count: hidden.count, showingScoutRuns: false,
                                     showingRecentlyDone: false, doneHours: hours))
        #expect(reason?.detail.contains("View options") == true)
        #expect(reason?.offersClearFilters == false)
    }

    @Test("the hidden-sessions message names only the view options still off")
    func hiddenMessageFollowsTheOptions() {
        func detail(_ scout: Bool, _ done: Bool, count: Int = 3) -> String {
            SessionsEmptyReason.allHidden(count: count, showingScoutRuns: scout,
                                          showingRecentlyDone: done, doneHours: 24).detail
        }
        let showScout = "Show Scout's own runs", showDone = "Show recently done"
        #expect(detail(false, false).contains(showScout) && detail(false, false).contains(showDone))
        #expect(!detail(true, false).contains(showScout) && detail(true, false).contains(showDone))
        #expect(detail(false, true).contains(showScout) && !detail(false, true).contains(showDone))
        #expect(detail(true, true) == "All 3 sessions finished more than 24 hours ago, so they're hidden.")
        #expect(detail(true, true, count: 1) == "Your one session finished more than 24 hours ago, so it's hidden.")
    }

    @Test("every state has its own symbol and a word")
    func stateSymbols() {
        let symbols = AgentSessionState.allCases.map(SessionStateStyle.symbol)
        #expect(Set(symbols).count == AgentSessionState.allCases.count)
        for state in AgentSessionState.allCases { #expect(!state.label.isEmpty) }
    }
}
