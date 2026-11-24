import SwiftUI

/// Editorial status console. Two-column page on wide windows: a primary
/// column with the hero / schedule / heatmap / sessions, and a rail with
/// budget, repo state, signals, and keyboard hints.
///
/// CC-8: run detail now opens as a right-side panel (preserving the
/// sessions list) and can be expanded to fill the main area on demand.
/// ⌘⇧F toggles expand/collapse, ⌘. closes.
struct ControlCenterView: View {
    @EnvironmentObject var state: AppState
    @State private var dayFilter: Date? = nil
    @State private var detail: DetailPresentation? = nil
    /// The page's width, for `railBelow(pageWidth:)`.
    @State private var pageWidth: CGFloat?

    /// Below this page width the rail moves under the main column. Beside it,
    /// a 320 pt rail left the Now / Today / Next strip about 450 pt at the
    /// minimum window, which cut each value to a few letters (UI audit C6).
    static let railBesideFrom: CGFloat = 1000

    nonisolated static func railBelow(pageWidth: CGFloat?) -> Bool {
        guard let pageWidth else { return false }
        return pageWidth < railBesideFrom
    }

    /// How the right-side detail panel is being shown — collapsed (alongside
    /// the rail), side (alongside the primary column), or full (overlays the
    /// whole main area).
    enum DetailPresentation: Equatable {
        case side(Run)
        case full(Run)

        var run: Run {
            switch self {
            case .side(let r): return r
            case .full(let r): return r
            }
        }
        var isFull: Bool { if case .full = self { return true } else { return false } }
    }

    /// True while a `.side` run-detail panel is open. The side panel is rendered
    /// inside `primaryColumn` next to the Sessions list (issue #54), so the rail
    /// hides in this mode — it's mutually exclusive with the side detail.
    private var isShowingSideDetail: Bool {
        if case .side = detail { return true } else { return false }
    }

    var body: some View {
        ZStack(alignment: .topTrailing) {
            mainSurface
            if let detail, detail.isFull {
                fullScreenDetail(detail.run)
                    .transition(.opacity)
            }
        }
        .background(
            Group {
                Button("") { closeDetail() }
                    .keyboardShortcut(AppShortcut.closeDetail)
                Button("") { toggleExpansion() }
                    .keyboardShortcut(AppShortcut.expandDetail)
            }
            .opacity(0)
            .frame(width: 0, height: 0)
        )
        .motionAnimation(.easeInOut(duration: 0.18), value: detail)
        .task { openPendingRunIfNeeded() }
        .onChange(of: state.pendingRunToOpen) { _, _ in openPendingRunIfNeeded() }
    }

    @ViewBuilder
    private var mainSurface: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                PowerStateBanner(service: state.powerStateService)
                ConnectorAlertBanner()
                // Only the banner follows the ledger, not the whole page.
                Observing(state.runLedgerService) {
                    RunHealthBanner(health: state.runLedgerService.health,
                                    firstSeen: state.runLedgerService.firstSeen) {
                        state.requestedSidebar = .health
                    }
                }
                header
                if Self.railBelow(pageWidth: pageWidth) {
                    VStack(alignment: .leading, spacing: 28) {
                        primaryColumn
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if !isShowingSideDetail {
                            rail
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                } else {
                    HStack(alignment: .top, spacing: 28) {
                        primaryColumn
                            .frame(maxWidth: .infinity, alignment: .leading)
                        // The side run-detail is rendered inside primaryColumn next
                        // to the Sessions list (issue #54) so it tracks that section
                        // rather than anchoring to the top of the whole column. The
                        // rail therefore shows whenever a side detail isn't open.
                        if !isShowingSideDetail {
                            rail
                                .frame(width: 320)
                        }
                    }
                }
            }
            .frame(maxWidth: 1180, alignment: .leading)
            .padding(.horizontal, 42)
            .padding(.top, 28)
            .padding(.bottom, 64)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .background(DS.Paper.base)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { pageWidth = $0 }
    }

    // MARK: - Detail panels

    private func sideDetail(_ run: Run) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            detailHeader(run: run, isExpanded: false)
            EditorialRule()
            RunDetailView(run: run)
        }
        .background(
            RoundedRectangle(cornerRadius: 12)  // ds-guard: not a card — the side detail panel, which floats with a shadow
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                .shadow(color: DS.Neumorphic.shadow.opacity(0.4), radius: 8, x: -2, y: 4)
        )
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func fullScreenDetail(_ run: Run) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            detailHeader(run: run, isExpanded: true)
            EditorialRule()
            RunDetailView(run: run)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Paper.base)
    }

    private func detailHeader(run: Run, isExpanded: Bool) -> some View {
        HStack(alignment: .center, spacing: 10) {
            Button { closeDetail() } label: {
                Image(systemName: "chevron.left")
                    .font(DS.font(.label, weight: .medium))
            }
            .buttonStyle(.plainHit)
            .foregroundStyle(DS.Ink.p3)
            .help(ShortcutLabel.help("Close", AppShortcut.closeDetail))
            .accessibilityLabel("Close run details")

            Text(run.displayName)
                .dsFont(.headline, .serif, weight: .medium)
                .foregroundStyle(DS.Ink.p1)
            Spacer()
            Button { toggleExpansion() } label: {
                Image(systemName: isExpanded
                      ? "arrow.down.right.and.arrow.up.left"
                      : "arrow.up.left.and.arrow.down.right")
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Ink.p3)
            }
            .buttonStyle(.plainHit)
            .help(ShortcutLabel.help(isExpanded ? "Collapse" : "Expand to full screen", AppShortcut.expandDetail))
            .accessibilityLabel(isExpanded ? "Collapse run details" : "Expand run details")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private func closeDetail() {
        detail = nil
    }

    private func toggleExpansion() {
        guard let d = detail else { return }
        detail = d.isFull ? .side(d.run) : .full(d.run)
    }

    /// Called by `SessionsListView` (via callback) when a row is tapped.
    fileprivate func openDetail(_ run: Run) {
        detail = .side(run)
    }

    /// Open a run requested from another tab and clear the intent, even when
    /// the id no longer resolves, so it can't fire later by surprise. (#43)
    private func openPendingRunIfNeeded() {
        guard let id = state.pendingRunToOpen else { return }
        state.pendingRunToOpen = nil
        if let run = state.sessionLogService.runs.first(where: { $0.id == id }) {
            detail = .side(run)
        }
    }

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            Text("Control Center")
                .dsFont(.display, .serif, weight: .medium)
                .foregroundStyle(DS.Ink.p1)
            Text("Status · Activity · Runs")
                .font(DS.font(.headline))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
        }
        .padding(.bottom, 16)
        .overlay(alignment: .bottom) { EditorialRule() }
        .padding(.bottom, 24)
    }

    // MARK: - Columns

    private var primaryColumn: some View {
        VStack(alignment: .leading, spacing: 20) {
            NowStripView()
            UpcomingStripView()
            ActivityHeatmapView(dayFilter: $dayFilter)
            // Run list + side detail share their own row so the detail
            // panel sits beside the Runs section instead of spanning the
            // full content height from the top (issue #54).
            HStack(alignment: .top, spacing: 28) {
                SessionsListView(
                    dayFilter: dayFilter,
                    onSelect: { run in openDetail(run) },
                    selectedRunID: detail?.run.id
                )
                .frame(maxWidth: .infinity, alignment: .leading)
                if case .side(let run) = detail {
                    sideDetail(run)
                        .frame(width: 460)
                }
            }
        }
    }

    private var rail: some View {
        VStack(alignment: .leading, spacing: 20) {
            UsageRailCard()
            RepoStateRailCard()
            ConnectorHealthRailCard()
            SignalsRailCard()
            KeyboardRailCard()
        }
    }
}

// MARK: - Rail cards

/// Small card header used throughout the rail.
struct RailCardHeader: View {
    let title: String
    var body: some View {
        Text(title.uppercased())
            .font(DS.font(.caption, weight: .medium))
            .tracking(0.06 * 11)
            .foregroundStyle(DS.Ink.p4)
            .padding(.bottom, 10)
    }
}

struct RepoStateRailCard: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            RailCardHeader(title: "Repo state")
            row("path",     Self.displayPath(state.scoutDirectory, home: FileManager.default.homeDirectoryForCurrentUser), DS.Ink.p1)
            // Read from the vault's own .git/HEAD; no row when there is no
            // repository. (The hard-coded "branch main" and "obsidian
            // mirrored" rows were placeholders: card C3.)
            if let branch = Self.currentBranch(vault: state.scoutDirectory) {
                row("branch", branch, DS.Ink.p1)
            }
        }
        .editorialCard(padding: 16)
    }

    /// The vault repository's checked-out branch, from `.git/HEAD`: the
    /// branch name, or a short commit id when HEAD is detached; nil without
    /// a readable repository. No `git` process, so it's cheap on every render.
    nonisolated static func currentBranch(vault: URL) -> String? {
        let head = vault.appendingPathComponent(".git/HEAD")
        guard let raw = try? String(contentsOf: head, encoding: .utf8) else { return nil }
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("ref: ") {
            let ref = String(text.dropFirst("ref: ".count))
            let name = ref.hasPrefix("refs/heads/") ? String(ref.dropFirst("refs/heads/".count)) : ref
            return name.isEmpty ? nil : name
        }
        let hex = CharacterSet(charactersIn: "0123456789abcdef")
        guard text.count >= 7, text.unicodeScalars.allSatisfy(hex.contains) else { return nil }
        return "detached at \(text.prefix(7))"
    }

    /// The vault path as the card shows it: `~`-abbreviated when it lives
    /// under `home`, absolute otherwise.
    nonisolated static func displayPath(_ url: URL, home: URL) -> String {
        let path = url.standardizedFileURL.path
        let homePath = home.standardizedFileURL.path
        if path == homePath { return "~" }
        let homePrefix = homePath.hasSuffix("/") ? homePath : homePath + "/"
        return path.hasPrefix(homePrefix) ? "~/" + String(path.dropFirst(homePrefix.count)) : path
    }

    private func row(_ key: String, _ value: String, _ color: Color) -> some View {
        HStack {
            Text(key)
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
            Text(value)
                .font(DS.font(.label, .mono))
                .foregroundStyle(color)
                // A real vault path can be long; keep the rail one line per row.
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }
}

/// One signal row backed by computed state. The whole card was previously
/// hard-coded — every signal here is now derived from the live services so
/// rows appear/disappear as conditions actually change.
struct ControlCenterSignal: Identifiable, Equatable {
    enum Severity: Equatable { case ok, warn, err }
    /// Where the row's button goes.
    enum Action: Equatable {
        case openHealth
        var title: String { "Open Health" }
    }
    let id: String
    let severity: Severity
    let tag: String
    let body: String
    var action: Action? = nil
}

private typealias Signal = ControlCenterSignal

struct SignalsRailCard: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        // The services this reads; AppState re-publishes none of their changes.
        Observing(state.sessionLogService, state.scheduleService, state.scoutUsageService) { observedBody }
    }

    @ViewBuilder private var observedBody: some View {
        VStack(alignment: .leading, spacing: 0) {
            RailCardHeader(title: "Signals")
            let signals = computeSignals()
            if signals.isEmpty {
                Text("All clear.")
                    .font(DS.font(.body))
                    .foregroundStyle(DS.Ink.p3)
                    .italic()
                    .padding(.top, 2)
            } else {
                ForEach(Array(signals.enumerated()), id: \.element.id) { idx, signal in
                    if idx > 0 { divider }
                    signalRow(signal)
                }
            }
        }
        .editorialCard(padding: 16)
    }

    private var divider: some View {
        EditorialRule().padding(.vertical, 8)
    }

    private func signalRow(_ signal: Signal) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Circle().fill(color(for: signal.severity)).frame(width: 6, height: 6)
                Text(signal.tag)
                    .font(DS.font(.caption, .mono, weight: .medium))
                    .foregroundStyle(color(for: signal.severity))
            }
            Text(signal.body)
                .font(DS.font(.body))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
            if let action = signal.action {
                Button(action.title) { perform(action) }
                    .buttonStyle(.ds(.link, size: .small))
                    .padding(.top, 2)
            }
        }
    }

    private func perform(_ action: Signal.Action) {
        switch action {
        case .openHealth: state.requestedSidebar = .health
        }
    }

    private func color(for severity: Signal.Severity) -> Color {
        switch severity {
        case .ok:   return DS.Status.ok
        case .warn: return DS.Status.warn
        case .err:  return DS.Status.err
        }
    }

    // MARK: - Signal derivation

    private func computeSignals() -> [Signal] {
        var out: [Signal] = []
        let runs = state.sessionLogService.runs
        let now = Date()

        if let limit = Self.usageLimitSignal(runs: runs, now: now) { out.append(limit) }

        // Research: only call this out if no schedule is configured. Once the
        // user adds a research schedule, the row disappears.
        let hasResearchSchedule = state.scheduleService.upcoming.contains { $0.type == .research }
        if !hasResearchSchedule {
            out.append(Signal(
                id: "research-missing",
                severity: .warn,
                tag: "RESEARCH",
                body: "Research isn't scheduled, so Scout doesn't look outward for news about your projects. Add it on the Schedules page."
            ))
        }

        // The same day and the same rule as the Today tile beside it.
        let today = NowStripView.todayRuns(runs, today: state.scoutUsageService.summary?.today, now: now)
        if let failed = Self.failedTodaySignal(todayRuns: today) { out.append(failed) }

        // Dreaming: report on the most recent dreaming run within the last
        // 36h so the message tracks reality (success vs. miss vs. failure).
        let cutoff = now.addingTimeInterval(-36 * 3600)
        let recentDreaming = runs.first { $0.type == .dreaming && $0.startedAt >= cutoff }
        if let d = recentDreaming {
            let when = d.startedAt.formatted(.relative(presentation: .named))
            switch d.status {
            case .success:
                let count = d.commits.count
                let body = count > 0
                    ? "Last run \(when) · \(count) commit\(count == 1 ? "" : "s")."
                    : "Last run \(when) · no commits."
                out.append(Signal(id: "dreaming", severity: .ok, tag: "DREAMING", body: body))
            case .running:
                out.append(Signal(
                    id: "dreaming",
                    severity: .warn,
                    tag: "DREAMING",
                    body: "Run started \(when) — still in progress."
                ))
            case .failure, .timeout, .orphaned, .rateLimited:
                out.append(Signal(
                    id: "dreaming",
                    severity: .err,
                    tag: "DREAMING",
                    body: "Last run \(when): \(d.status.displayName). Open it in the list below to see why."
                ))
            default:
                break
            }
        } else if state.scheduleService.upcoming.contains(where: { $0.type == .dreaming }) {
            // Schedule exists but nothing fired in the last 36h — likely a
            // missed window worth surfacing.
            out.append(Signal(
                id: "dreaming",
                severity: .warn,
                tag: "DREAMING",
                body: "No overnight run in the last 36 hours. Check Settings ▸ Engine to see whether scheduled runs are installed."
            ))
        }

        // The connector roster fallback is said once, on the Connector
        // health card it affects (UI audit R10, #30), not here as well.

        return out
    }

    /// A Claude usage limit in the last day, as the engine classified it: a
    /// run whose ledger `failure_class` is `rate_limited` (or, before its
    /// ledger row lands, whose runner logged "Rate limit detected"). Never a
    /// log line that merely mentions "rate" or "429": a sign-in race
    /// (`auth_race`) or a session talking about limits isn't a usage limit.
    static func usageLimitSignal(runs: [Run], now: Date) -> ControlCenterSignal? {
        let dayAgo = now.addingTimeInterval(-24 * 3600)
        guard let r = runs.first(where: { $0.startedAt >= dayAgo && $0.status == .rateLimited }) else { return nil }
        return ControlCenterSignal(
            id: "usage-limit",
            severity: .warn,
            tag: "USAGE LIMIT",
            body: "\(r.displayName) hit your Claude plan's usage limit \(r.startedAt.formatted(.relative(presentation: .named))). Scout tries again after it resets.")
    }

    /// Today's failed runs (`RunOutcomeDisplay.isFailure`, the Today tile's
    /// rule), with their reason when they share one, and the Health page,
    /// where each has its log and what to do. A day whose failures all clear
    /// by themselves (`Problem.softFailureClasses`: a usage limit, a busy
    /// login, the network, a busy folder) is amber, as the Health page has them.
    static func failedTodaySignal(todayRuns: [Run]) -> ControlCenterSignal? {
        let failed = todayRuns.filter { RunOutcomeDisplay.isFailure($0.status) }
        guard !failed.isEmpty else { return nil }
        let n = failed.count
        let reasons = Set(failed.map { RunOutcomeDisplay.failureReason($0) ?? "" })
        var body = "\(n) run\(n == 1 ? "" : "s") failed today"
        // The reason keeps its own capitals: it can start with a name ("Claude").
        if reasons.count == 1, let reason = reasons.first, !reason.isEmpty {
            body += " (\(reason))"
        }
        body += "."
        let soft = failed.allSatisfy { RunOutcomeDisplay.failureClass($0).map(Problem.isSoftFailureClass) ?? false }
        return ControlCenterSignal(id: "failures", severity: soft ? .warn : .err, tag: "FAILED RUNS",
                                   body: body, action: .openHealth)
    }
}

/// Only shortcuts that exist on this page (bound in `ControlCenterView`'s
/// background buttons), plus the system Settings one. The card used to list
/// ⌘1, ⌘2, ⌘R, ⌘K and ⌘↵, none of which were bound (card C3). Each row's
/// keys come from the `AppShortcut` its button binds.
struct KeyboardRailCard: View {
    static let rows: [(shortcut: KeyboardShortcut, label: String)] = [
        (AppShortcut.closeDetail, "Close run details"),
        (AppShortcut.expandDetail, "Expand run details"),
        (AppShortcut.settings, "Settings"),
    ]
    static var shortcuts: [(keys: String, label: String)] {
        rows.map { (ShortcutLabel.text($0.shortcut), $0.label) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            RailCardHeader(title: "Keyboard")
            ForEach(Self.shortcuts, id: \.keys) { kb($0.keys, $0.label) }
        }
        .editorialCard(padding: 16)
    }

    private func kb(_ keys: String, _ label: String) -> some View {
        HStack(spacing: 10) {
            Text(keys)
                .font(DS.font(.label, .mono, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
                .frame(width: 32, alignment: .leading)
            Text(label)
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
        }
    }
}
