import AppKit
import SwiftUI

/// The Sessions page (spec §6): every local Claude Code session by project and
/// state. Master (header + board or table) beside a 380 pt detail pane, as on
/// the Schedules page. Watching runs only while this page is on screen.
struct SessionsView: View {
    @EnvironmentObject var service: SessionIndexService
    @Environment(\.openSettings) private var openSettings

    @SceneStorage("sessionsView") private var viewMode: SessionsViewMode = .board
    @State private var filter = SessionsFilter()
    @State private var selectedID: String?
    /// The page's own width, for `DetailPane.layout`. nil until first laid out.
    @State private var pageWidth: CGFloat?

    /// The shared master–detail width rule (UI audit C6).
    typealias DetailPane = DetailPaneLayout

    var body: some View {
        let now = Date()
        let pane = DetailPane.layout(pageWidth: pageWidth, hasSelection: selected != nil)
        HStack(spacing: 0) {
            master(now: now)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            if case .shown(let width, let closable) = pane {
                Divider()
                SessionDetailView(session: selected, index: service.index, now: now) { selectedID = $0 }
                    .frame(width: width)
                    .overlay(alignment: .topTrailing) {
                        if closable {
                            DetailPaneCloseButton(help: "Close session details") { selectedID = nil }
                        }
                    }
            }
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { pageWidth = $0 }
        // Esc closes only the compact pane; at full width the pane stays.
        .onExitCommand { if case .shown(_, closable: true) = pane { selectedID = nil } }
        .background(DS.Paper.base)
        .onAppear {
            service.setAppVisible(NSApp?.occlusionState.contains(.visible) ?? true)
            service.setVisible(true)
        }
        .onDisappear { service.setVisible(false) }
        // Minimised, hidden (Cmd-H), fully covered or on another Space: not
        // being looked at, so stop the 2 s cadence until it is back on screen.
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didChangeOcclusionStateNotification)) { _ in
            service.setAppVisible(NSApp?.occlusionState.contains(.visible) ?? true)
        }
    }

    private var selected: AgentSession? {
        guard let id = selectedID else { return nil }
        return service.index?.sessions.first { $0.id == id }
    }

    private func master(now: Date) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            SessionsHeader(
                viewMode: $viewMode,
                filter: $filter,
                counts: service.index.map { SessionsLayout.stateCounts(index: $0, filter: filter, now: now) } ?? [:],
                projects: service.index.map(SessionsLayout.menuProjects(index:)) ?? [],
                service: service
            )
            Divider().background(DS.Rule.hard)
            banner
            content(now: now)
        }
    }

    @ViewBuilder
    private func content(now: Date) -> some View {
        if let index = service.index {
            switch viewMode {
            case .board:
                SessionsBoardView(index: index, filter: filter, now: now, selectedID: $selectedID,
                                  onClearFilters: clearFilters)
            case .table:
                if let reason = SessionsLayout.emptyReason(index: index, filter: filter, now: now) {
                    // An empty Table is a blank grid; say why instead (#37).
                    SessionsEmptyStateView(reason: reason, onClearFilters: clearFilters)
                        .padding(.top, 56)
                        .frame(maxHeight: .infinity, alignment: .top)
                } else {
                    SessionsTableView(
                        rows: SessionsLayout.tableRows(index: index, filter: filter, now: now),
                        now: now,
                        selectedID: $selectedID
                    )
                }
            }
        } else {
            VStack(spacing: 10) {
                Spacer()
                if service.availability == .ok && service.lastError == nil {
                    ProgressView()
                    Text("Reading your Claude Code sessions…")
                        .font(DS.sans(13))
                        .foregroundStyle(DS.Ink.p3)
                }
                Spacer()
            }
            .frame(maxWidth: .infinity)
        }
    }

    /// The empty state's "Clear filters": the search, project and state
    /// chips. The two view options stay as they are; they aren't filters.
    private func clearFilters() {
        filter.states = []
        filter.projectKey = nil
        filter.search = ""
    }

    // MARK: Banners

    @ViewBuilder
    private var banner: some View {
        if let problem = Self.problem(availability: service.availability,
                                      lastError: service.lastError,
                                      unreadable: service.index?.unreadableSessions ?? 0) {
            ProblemView(problem: problem, actions: actions(for: problem))
                .padding(.horizontal, 24)
                .padding(.vertical, 8)
        }
    }

    private func actions(for problem: Problem) -> [ProblemAction] {
        switch problem.severity {
        case .info: return []
        default:
            return [.retry { Task { await service.refreshFast() } },
                    // The fix line says Settings ▸ Engine, so land there (R2).
                    ProblemAction("Open Settings") { HelpActions.showEngineSettings(openSettings) }]
        }
    }

    /// What the banner says, in plain words; the engine's own message goes
    /// to Details.
    static func problem(availability: SessionIndexService.Availability,
                        lastError: String?, unreadable: Int) -> Problem? {
        switch availability {
        case .engineTooOld:
            return .engineTooOld("Sessions", details: lastError
                ?? "Sessions needs scout-plugin 0.11.0 or later (scoutctl session index). In Claude Code: /scout-update")
        case .engineMissing:
            return .engineUnavailable("Sessions", details: lastError ?? "scoutctl not found")
        case .unsupportedSchema(let version):
            return Problem(
                severity: .warning,
                title: "This version of Scout can't read the newest session list",
                impact: "You're seeing the last list it could read.",
                fix: "Update Scout from Scout ▸ Check for Updates….",
                details: "Session index schema v\(version); this app reads v\(SessionIndex.supportedSchemaVersion).")
        case .ok:
            if let lastError {
                return Problem(
                    severity: .error,
                    title: "Couldn't refresh the session list",
                    impact: "The list may be out of date.",
                    fix: "Try again. If it keeps failing, copy the details when asking for help.",
                    details: lastError)
            }
            if unreadable > 0 {
                return Problem(
                    severity: .info,
                    title: "\(unreadable) session\(unreadable == 1 ? "" : "s") couldn't be read and \(unreadable == 1 ? "isn't" : "aren't") shown.")
            }
            return nil
        }
    }
}
