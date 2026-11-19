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

/// Why the Sessions page has nothing to show (UI audit #37).
nonisolated enum SessionsEmptyReason: Equatable, Sendable {
    /// Claude Code hasn't recorded a session on this Mac yet.
    case noSessionsYet
    /// There are sessions, but the view options hide every one: Scout's own
    /// runs unless shown, finished ones unless recently done are shown (and
    /// then those older than `doneHours`).
    case allHidden(count: Int, showingScoutRuns: Bool, showingRecentlyDone: Bool, doneHours: Int)
    /// The search, project or state filters match nothing.
    case noMatches

    var title: String {
        switch self {
        case .noSessionsYet: return "No Claude Code sessions yet"
        case .allHidden: return "Nothing to show right now"
        case .noMatches: return "No sessions match these filters."
        }
    }

    var detail: String {
        switch self {
        case .noSessionsYet:
            return "Sessions you start in Claude Code show up here, by project, as soon as they begin."
        case .allHidden(let count, let scoutRuns, let recentlyDone, let hours):
            let one = count == 1
            let subject = one ? "Your one session" : "All \(count) sessions"
            let are = one ? "is" : "are"
            let showScout = "\u{201C}Show Scout's own runs\u{201D}"
            let showDone = "\u{201C}Show recently done\u{201D}"
            let older = "finished more than \(hours) hour\(hours == 1 ? "" : "s") ago"
            switch (scoutRuns, recentlyDone) {
            case (false, false):
                return "\(subject) \(are) Scout's own runs or finished, which are hidden. "
                    + "Turn on \(showScout) or \(showDone) in View options to see them."
            case (true, false):
                return "\(subject) \(are) finished, which \(one ? "is" : "are") hidden. "
                    + "Turn on \(showDone) in View options to see the ones from the last \(hours) hour\(hours == 1 ? "" : "s")."
            case (false, true):
                return "\(subject) \(are) Scout's own runs or \(older). "
                    + "Turn on \(showScout) in View options to see Scout's runs."
            case (true, true):
                return "\(subject) \(older), so \(one ? "it's" : "they're") hidden."
            }
        case .noMatches:
            return "Clear the search, project and state filters to see every session."
        }
    }

    /// Only chosen filters can be cleared; the other two aren't filters.
    var offersClearFilters: Bool { self == .noMatches }
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
        // The project's own name, not the disambiguated one: this runs per
        // session per keystroke, and the name alone already matches.
        let project = index.projects.first { $0.key == session.projectKey }?.name ?? basename(session.projectKey)
        var haystack = [session.displayTitle, project]
        if let prompt = session.transcript?.firstPrompt { haystack.append(prompt) }
        if let branch = session.worktree?.branch { haystack.append(branch) }
        if let group = session.groupName { haystack.append(group) }
        for pr in session.prs { haystack.append("#\(pr.number)"); haystack.append(pr.repo) }
        return haystack.contains { $0.localizedStandardContains(needle) }
    }

    static func visibleSessions(index: SessionIndex, filter: SessionsFilter, now: Date) -> [AgentSession] {
        index.sessions.filter { isVisible($0, filter: filter, index: index, now: now) }
    }

    /// Why nothing is on the board or in the table, or nil when something
    /// is (UI audit #37): a first run with no sessions at all reads
    /// differently from filters that match nothing.
    static func emptyReason(index: SessionIndex, filter: SessionsFilter, now: Date) -> SessionsEmptyReason? {
        guard visibleSessions(index: index, filter: filter, now: now).isEmpty else { return nil }
        if index.sessions.isEmpty { return .noSessionsYet }
        let chosen = !filter.states.isEmpty || filter.projectKey != nil
            || !filter.search.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return chosen ? .noMatches : .allHidden(
            count: index.sessions.count, showingScoutRuns: filter.showScoutRuns,
            showingRecentlyDone: filter.showRecentlyDone, doneHours: index.display.doneVisibleHours)
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

    /// One row per project with a visible session, most recently active
    /// first. A card in the Now strip is not repeated in its project's lane
    /// (UI audit #34), so a project whose every visible session is in Now
    /// has no lane; its Now cards carry the project name instead.
    static func rows(index: SessionIndex, filter: SessionsFilter, now: Date) -> [SessionRow] {
        let inNow = Set(nowStrip(index: index, filter: filter, now: now).map(\.id))
        let visible = visibleSessions(index: index, filter: filter, now: now).filter { !inNow.contains($0.id) }
        let grouped = Dictionary(grouping: visible, by: \.projectKey)
        let names = projectNames(in: index)
        let rows = grouped.map { key, sessions -> SessionRow in
            let sorted = sessions.sorted(by: severityOrder)
            return SessionRow(
                id: key,
                name: names[key] ?? basename(key),
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
        let names = projectNames(in: index)
        return visibleSessions(index: index, filter: filter, now: now)
            .sorted(by: severityOrder)
            .map { SessionTableRow(session: $0, projectName: names[$0.projectKey] ?? basename($0.projectKey)) }
    }

    // MARK: Lookups

    /// A project's name as every part of the page shows it: lane, menu,
    /// table, detail pane, handoff. Same-named projects are told apart.
    static func projectName(for key: String, in index: SessionIndex,
                            home: String = FileManager.default.homeDirectoryForCurrentUser.path) -> String {
        projectNames(in: index, home: home)[key] ?? basename(key)
    }

    /// Every project key in the index (its projects, then any key only a
    /// session names, under its folder's name) to the name the page shows,
    /// disambiguated by `DisplayName.projectNames`.
    static func projectNames(in index: SessionIndex,
                             home: String = FileManager.default.homeDirectoryForCurrentUser.path) -> [String: String] {
        let listed = index.projects.map { (key: $0.key, name: $0.name) }
        let unlisted = index.sessions.map(\.projectKey).map { (key: $0, name: basename($0)) }
        return DisplayName.projectNames(listed + unlisted, home: home)
    }

    static func basename(_ key: String) -> String {
        let base = (key as NSString).lastPathComponent
        return base.isEmpty ? key : base
    }

    /// Every project in the index, by its shown name, for the project menu.
    /// (No default `home`: the page passes this as a function reference.)
    static func menuProjects(index: SessionIndex) -> [SessionProject] {
        menuProjects(index: index, home: FileManager.default.homeDirectoryForCurrentUser.path)
    }

    static func menuProjects(index: SessionIndex, home: String) -> [SessionProject] {
        let names = projectNames(in: index, home: home)
        return index.projects
            .map { SessionProject(key: $0.key, name: names[$0.key] ?? $0.name, groupID: $0.groupID, counts: $0.counts) }
            .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    static func parent(of session: AgentSession, in index: SessionIndex) -> AgentSession? {
        guard let parentID = session.parentSessionID else { return nil }
        return index.sessions.first { $0.id == parentID }
    }

    static func children(of session: AgentSession, in index: SessionIndex) -> [AgentSession] {
        index.sessions.filter { $0.parentSessionID == session.id }.sorted(by: recencyOrder)
    }
}
