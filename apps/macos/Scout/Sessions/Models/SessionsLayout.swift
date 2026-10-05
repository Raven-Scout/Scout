import Foundation

/// What the header's controls select. Shared by the board and the table.
nonisolated struct SessionsFilter: Equatable, Sendable {
    /// Empty means every state.
    var states: Set<AgentSessionState> = []
    var projectKey: String? = nil
    var search: String = ""
    var showScoutRuns: Bool = false
    /// Done sessions appear only when this is on, and then only those active
    /// within the index's `done_visible_hours` (spec §6.1).
    var showRecentlyDone: Bool = false
}

/// One project swimlane on the board.
nonisolated struct SessionRow: Identifiable, Equatable, Sendable {
    /// The project key.
    let id: String
    let name: String
    /// needs_you, running, waiting, parked — in severity order.
    let active: [AgentSession]
    /// Collapsed behind "Stale (n)".
    let stale: [AgentSession]
    /// Collapsed behind "Done (n)"; empty unless recently-done is on.
    let done: [AgentSession]
    let lastActivity: Date?

    var counts: [AgentSessionState: Int] {
        Dictionary(grouping: active + stale + done, by: \.state).mapValues(\.count)
    }
}

/// A table row: the session plus the values its sortable columns compare.
nonisolated struct SessionTableRow: Identifiable, Equatable, Sendable {
    let session: AgentSession
    let projectName: String

    var id: String { session.id }
    var severity: Int { session.state.severity * 2 + (session.state == .parked && !session.isOpen ? 1 : 0) }
    var title: String { session.displayTitle }
    var reason: String { session.primaryReason ?? "" }
    var prNumber: Int { session.pr?.number ?? 0 }
    var lastActivity: Date { session.lastActivityAt ?? .distantPast }
    var model: String { session.model ?? "" }
    var turns: Int { session.turns ?? 0 }
}

/// Pure layout for the Sessions page (spec §6.2–6.3). Every view reads its
/// rows from here, so the rules are tested without rendering anything.
nonisolated enum SessionsLayout {

    // MARK: Filtering

    /// Every rule except the state chips. The chips' counts come from this set,
    /// so selecting one chip does not zero the others.
    static func passesNonStateFilters(
        _ session: AgentSession, filter: SessionsFilter, index: SessionIndex, now: Date
    ) -> Bool {
        if session.isScoutRun && !filter.showScoutRuns { return false }
        if let key = filter.projectKey, session.projectKey != key { return false }
        if session.state == .done && !(filter.showRecentlyDone && isRecentlyDone(session, index: index, now: now)) {
            return false
        }
        return matchesSearch(session, query: filter.search, index: index)
    }

    static func isVisible(_ session: AgentSession, filter: SessionsFilter, index: SessionIndex, now: Date) -> Bool {
        (filter.states.isEmpty || filter.states.contains(session.state))
            && passesNonStateFilters(session, filter: filter, index: index, now: now)
    }

    static func isRecentlyDone(_ session: AgentSession, index: SessionIndex, now: Date) -> Bool {
        guard let last = session.lastActivityAt else { return false }
        return now.timeIntervalSince(last) <= Double(index.display.doneVisibleHours) * 3600
    }

    static func matchesSearch(_ session: AgentSession, query: String, index: SessionIndex) -> Bool {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return true }
        var haystack = [session.displayTitle, projectName(for: session.projectKey, in: index)]
        if let prompt = session.transcript?.firstPrompt { haystack.append(prompt) }
        if let branch = session.worktree?.branch { haystack.append(branch) }
        if let group = session.groupName { haystack.append(group) }
        for pr in session.prs { haystack.append("#\(pr.number)"); haystack.append(pr.repo) }
        return haystack.contains { $0.localizedStandardContains(needle) }
    }

    static func visibleSessions(index: SessionIndex, filter: SessionsFilter, now: Date) -> [AgentSession] {
        index.sessions.filter { isVisible($0, filter: filter, index: index, now: now) }
    }

    // MARK: Ordering

    /// Severity, then open before closed within parked, then most recent
    /// first, then id so equal sessions never swap between refreshes.
    static func severityOrder(_ a: AgentSession, _ b: AgentSession) -> Bool {
        if a.state.severity != b.state.severity { return a.state.severity < b.state.severity }
        if a.state == .parked && a.isOpen != b.isOpen { return a.isOpen }
        let la = a.lastActivityAt ?? .distantPast
        let lb = b.lastActivityAt ?? .distantPast
        if la != lb { return la > lb }
        return a.id < b.id
    }

    static func recencyOrder(_ a: AgentSession, _ b: AgentSession) -> Bool {
        let la = a.lastActivityAt ?? .distantPast
        let lb = b.lastActivityAt ?? .distantPast
        if la != lb { return la > lb }
        return a.id < b.id
    }

    // MARK: Board

    /// One row per project with a visible session, most recently active first.
    static func rows(index: SessionIndex, filter: SessionsFilter, now: Date) -> [SessionRow] {
        let visible = visibleSessions(index: index, filter: filter, now: now)
        let grouped = Dictionary(grouping: visible, by: \.projectKey)
        let rows = grouped.map { key, sessions -> SessionRow in
            let sorted = sessions.sorted(by: severityOrder)
            return SessionRow(
                id: key,
                name: projectName(for: key, in: index),
                active: sorted.filter { $0.state != .stale && $0.state != .done },
                stale: sorted.filter { $0.state == .stale },
                done: sorted.filter { $0.state == .done },
                lastActivity: sessions.compactMap(\.lastActivityAt).max()
            )
        }
        return rows.sorted { a, b in
            let la = a.lastActivity ?? .distantPast
            let lb = b.lastActivity ?? .distantPast
            if la != lb { return la > lb }
            return a.name.localizedStandardCompare(b.name) == .orderedAscending
        }
    }

    /// Needs-you cards, then running cards, across every project (spec §6.2).
    static func nowStrip(index: SessionIndex, filter: SessionsFilter, now: Date) -> [AgentSession] {
        let visible = visibleSessions(index: index, filter: filter, now: now)
        let needsYou = visible.filter { $0.state == .needsYou }.sorted(by: recencyOrder)
        let running = visible.filter { $0.state == .running }.sorted(by: recencyOrder)
        return needsYou + running
    }

    /// Counts for the state chips: every filter but the chips themselves.
    static func stateCounts(index: SessionIndex, filter: SessionsFilter, now: Date) -> [AgentSessionState: Int] {
        var counts: [AgentSessionState: Int] = [:]
        for session in index.sessions where passesNonStateFilters(session, filter: filter, index: index, now: now) {
            counts[session.state, default: 0] += 1
        }
        return counts
    }

    /// The sidebar badge: sessions that need you, never Scout's own runs.
    static func needsYouCount(in index: SessionIndex) -> Int {
        index.sessions.filter { $0.state == .needsYou && !$0.isScoutRun && !$0.isArchived }.count
    }

    // MARK: Table

    static func tableRows(index: SessionIndex, filter: SessionsFilter, now: Date) -> [SessionTableRow] {
        visibleSessions(index: index, filter: filter, now: now)
            .sorted(by: severityOrder)
            .map { SessionTableRow(session: $0, projectName: projectName(for: $0.projectKey, in: index)) }
    }

    // MARK: Lookups

    static func projectName(for key: String, in index: SessionIndex) -> String {
        if let project = index.projects.first(where: { $0.key == key }) { return project.name }
        let base = (key as NSString).lastPathComponent
        return base.isEmpty ? key : base
    }

    /// Every project in the index, by name, for the project menu.
    static func menuProjects(index: SessionIndex) -> [SessionProject] {
        index.projects.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    static func parent(of session: AgentSession, in index: SessionIndex) -> AgentSession? {
        guard let parentID = session.parentSessionID else { return nil }
        return index.sessions.first { $0.id == parentID }
    }

    static func children(of session: AgentSession, in index: SessionIndex) -> [AgentSession] {
        index.sessions.filter { $0.parentSessionID == session.id }.sorted(by: recencyOrder)
    }
}
