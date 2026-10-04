import AppKit
import SwiftUI

/// The Sessions page (spec §6): every local Claude Code session by project and
/// state. Master (header + board or table) beside a 380 pt detail pane, as on
/// the Schedules page. Watching runs only while this page is on screen.
struct SessionsView: View {
    @EnvironmentObject var service: SessionIndexService

    @SceneStorage("sessionsView") private var viewMode: SessionsViewMode = .board
    @State private var filter = SessionsFilter()
    @State private var selectedID: String?

    var body: some View {
        let now = Date()
        HStack(spacing: 0) {
            master(now: now)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            Divider()
            SessionDetailView(session: selected, index: service.index, now: now) { selectedID = $0 }
                .frame(width: 380)
        }
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
                SessionsBoardView(index: index, filter: filter, now: now, selectedID: $selectedID)
            case .table:
                SessionsTableView(
                    rows: SessionsLayout.tableRows(index: index, filter: filter, now: now),
                    now: now,
                    selectedID: $selectedID
                )
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

    // MARK: Banners

    @ViewBuilder
    private var banner: some View {
        switch service.availability {
        case .engineTooOld:
            bannerRow("Sessions needs scout-plugin 0.11.0 or later. Run /scout-update, then come back.",
                      symbol: "exclamationmark.triangle.fill", tint: DS.Status.warn)
        case .engineMissing:
            bannerRow(service.lastError ?? "scoutctl not found — check that scout-plugin is installed.",
                      symbol: "xmark.octagon.fill", tint: DS.Status.err)
        case .unsupportedSchema(let version):
            bannerRow("The session index is schema v\(version); this Scout reads v\(SessionIndex.supportedSchemaVersion). Update Scout. Showing the last index it could read.",
                      symbol: "exclamationmark.triangle.fill", tint: DS.Status.warn)
        case .ok:
            if let error = service.lastError {
                bannerRow(error, symbol: "xmark.octagon.fill", tint: DS.Status.err)
            } else if let unreadable = service.index?.unreadableSessions, unreadable > 0 {
                bannerRow("\(unreadable) session\(unreadable == 1 ? "" : "s") in the index couldn't be read and \(unreadable == 1 ? "is" : "are") not shown.",
                          symbol: "info.circle", tint: DS.Ink.p3)
            }
        }
    }

    private func bannerRow(_ text: String, symbol: String, tint: Color) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: symbol).foregroundStyle(tint)
            Text(text).font(DS.sans(12.5)).foregroundStyle(DS.Ink.p1).textSelection(.enabled)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 9)
        .background(tint.opacity(0.12))
    }
}
