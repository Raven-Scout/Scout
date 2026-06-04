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
                    .keyboardShortcut(".", modifiers: .command)
                Button("") { toggleExpansion() }
                    .keyboardShortcut("f", modifiers: [.command, .shift])
            }
            .opacity(0)
            .frame(width: 0, height: 0)
        )
        .animation(.easeInOut(duration: 0.18), value: detail)
    }

    @ViewBuilder
    private var mainSurface: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                PowerStateBanner(service: state.powerStateService)
                ConnectorAlertBanner()
                header
                HStack(alignment: .top, spacing: 28) {
                    primaryColumn
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if case .side(let run) = detail {
                        sideDetail(run)
                            .frame(width: 460)
                    } else {
                        rail
                            .frame(width: 320)
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
    }

    // MARK: - Detail panels

    private func sideDetail(_ run: Run) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            detailHeader(run: run, isExpanded: false)
            EditorialRule()
            RunDetailView(run: run)
        }
        .background(
            RoundedRectangle(cornerRadius: 12)
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
                    .font(.system(size: 12, weight: .medium))
            }
            .buttonStyle(.plainHit)
            .foregroundStyle(DS.Ink.p3)
            .help("Close (⌘.)")

            Text(run.displayName)
                .font(DS.serif(16, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
            Spacer()
            Button { toggleExpansion() } label: {
                Image(systemName: isExpanded
                      ? "arrow.down.right.and.arrow.up.left"
                      : "arrow.up.left.and.arrow.down.right")
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Ink.p3)
            }
            .buttonStyle(.plainHit)
            .help(isExpanded ? "Collapse (⌘⇧F)" : "Expand to full screen (⌘⇧F)")
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

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            Text("Control Center")
                .font(DS.serif(28, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
            Text("Status · Activity · Sessions")
                .font(DS.sans(14))
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
            SessionsListView(
                dayFilter: dayFilter,
                onSelect: { run in openDetail(run) },
                selectedRunID: detail?.run.id
            )
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
            .font(DS.sans(11, weight: .medium))
            .tracking(0.06 * 11)
            .foregroundStyle(DS.Ink.p4)
            .padding(.bottom, 10)
    }
}

struct RepoStateRailCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            RailCardHeader(title: "Repo state")
            row("path",     "~/Scout",  DS.Ink.p1)
            row("branch",   "main",     DS.Ink.p1)
            row("obsidian", "mirrored", DS.Status.ok)
        }
        .editorialCard(padding: 16)
    }

    private func row(_ key: String, _ value: String, _ color: Color) -> some View {
        HStack {
            Text(key)
                .font(DS.sans(12))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
            Text(value)
                .font(DS.mono(12))
                .foregroundStyle(color)
        }
    }
}

/// One signal row backed by computed state. The whole card was previously
/// hard-coded — every signal here is now derived from the live services so
/// rows appear/disappear as conditions actually change.
private struct Signal: Identifiable {
    enum Severity { case ok, warn, err }
    let id: String
    let severity: Severity
    let tag: String
    let body: String
}

struct SignalsRailCard: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            RailCardHeader(title: "Signals")
            let signals = computeSignals()
            if signals.isEmpty {
                Text("All clear.")
                    .font(DS.sans(13))
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
                    .font(DS.mono(11, weight: .medium))
                    .foregroundStyle(color(for: signal.severity))
            }
            Text(signal.body)
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
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
        let cal = Calendar.current
        let now = Date()

        // Anthropic API: surfaces only when there's an actual rate-limit /
        // 429 / overload signal in the last 24h. No live state? No row.
        let dayAgo = now.addingTimeInterval(-24 * 3600)
        let recentRateLimited = runs.first { run in
            run.startedAt >= dayAgo && (
                run.status == .rateLimited ||
                run.errorsDetected.contains { err in
                    let p = err.pattern.lowercased()
                    return p.contains("429") || p.contains("rate") ||
                           p.contains("overload") || p.contains("throttle") ||
                           p.contains("quota")
                }
            )
        }
        if let r = recentRateLimited {
            out.append(Signal(
                id: "anthropic",
                severity: .err,
                tag: "ANTHROPIC API",
                body: "Rate-limited \(r.startedAt.formatted(.relative(presentation: .named))) — check live status, top-up may be needed."
            ))
        }

        // Research: only call this out if no schedule is configured. Once the
        // user adds a research plist, the row disappears.
        let hasResearchSchedule = state.scheduleService.upcoming.contains { $0.type == .research }
        if !hasResearchSchedule {
            out.append(Signal(
                id: "research-missing",
                severity: .warn,
                tag: "RESEARCH",
                body: "No schedule configured — heartbeat skips research every dispatch."
            ))
        }

        // Failed runs today: surfaces a count rather than a static message,
        // so the row reflects the day's actual situation.
        let failedToday = runs.filter {
            cal.isDateInToday($0.startedAt) &&
            [.failure, .timeout].contains($0.status)
        }
        if !failedToday.isEmpty {
            let n = failedToday.count
            out.append(Signal(
                id: "failures",
                severity: .err,
                tag: "FAILED RUNS",
                body: "\(n) run\(n == 1 ? "" : "s") failed today — open Sessions to triage."
            ))
        }

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
                    body: "Last run \(when) ended in \(d.status.rawValue) — investigate."
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
                body: "No overnight run in the last 36h — check launchd."
            ))
        }

        // Connector roster fallback: ConnectorHealthService publishes a
        // reason when it can't read the canonical roster — surface it here
        // so the user knows why the matrix may be stale.
        if let reason = state.connectorHealthService.rosterFallbackReason {
            out.append(Signal(
                id: "connectors",
                severity: .warn,
                tag: "CONNECTORS",
                body: "Roster fallback: \(reason)"
            ))
        }

        return out
    }
}

struct KeyboardRailCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            RailCardHeader(title: "Keyboard")
            kb("⌘1", "Control Center")
            kb("⌘2", "Action Items")
            kb("⌘R", "Run briefing now")
            kb("⌘K", "Quick find")
            kb("⌘↵", "Mark done (on task)")
        }
        .editorialCard(padding: 16)
    }

    private func kb(_ keys: String, _ label: String) -> some View {
        HStack(spacing: 10) {
            Text(keys)
                .font(DS.mono(12, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
                .frame(width: 32, alignment: .leading)
            Text(label)
                .font(DS.sans(12.5))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
        }
    }
}
