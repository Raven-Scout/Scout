import SwiftUI

/// What Help ▸ Run Doctor… shows, worked out from the engine's state and its
/// last `scoutctl bootstrap doctor` (app audit C11). Pure, for tests.
struct DoctorPresentation: Equatable {
    struct Item: Equatable, Hashable {
        let text: String
        var fix: String? = nil
        var since: String? = nil
        /// The files a grouped warning stands for, behind a disclosure (UI audit R1).
        var files: [String] = []
    }
    struct Group: Equatable {
        let title: String
        let tone: Tone
        let items: [Item]
    }
    enum Tone: Equatable { case ok, warn, error, neutral }

    let headline: String
    let tone: Tone
    let groups: [Group]
    /// Whether the doctor can run for this engine state (mirrors `EngineHealthService.refresh()`).
    let canRun: Bool

    init(state: EngineState, doctor: DoctorReport?, lastError: String?) {
        let isBroken: Bool = { if case .broken = state { return true }; return false }()
        canRun = state.scoutctl != nil && (!state.gatesTabs || state.isManaged) && !isBroken
        if case .notInstalled = state {
            headline = "Scout's engine isn't installed yet"
            tone = .warn
            groups = [Group(title: "What to do", tone: .neutral,
                            items: [Item(text: "Finish setting up Scout in Settings ▸ Engine.")])]
            return
        }
        if case .broken(_, let reason) = state {
            headline = "Scout's engine needs repair"
            tone = .error
            groups = [Group(title: "Problem", tone: .error, items: [Item(text: reason)])]
            return
        }
        guard let doctor else {
            headline = lastError == nil ? "Doctor hasn't run yet" : "Doctor couldn't run"
            tone = lastError == nil ? .neutral : .error
            groups = lastError.map { [Group(title: "Problem", tone: .error, items: [Item(text: $0)])] } ?? []
            return
        }
        // The same issues and count as Settings, the Health page and the status bar (UI audit R1).
        let summary = HealthSummary(doctor: doctor)
        let count = summary.attentionLabel.map { " · \($0)" } ?? ""
        switch (doctor.severity, summary.severity) {
        case (.red, _): headline = "Scout's engine has problems\(count)"; tone = .error
        case (.yellow, _): headline = "Scout's engine works, with warnings\(count)"; tone = .warn
        case (.green, .green): headline = "Scout's engine is healthy"; tone = .ok
        // A healthy install whose runs aren't: say so, rather than "healthy" beside a warning.
        case (.green, .red): headline = "Scheduled runs need fixing\(count)"; tone = .error
        case (.green, .yellow): headline = "Scheduled runs need a look\(count)"; tone = .warn
        }
        func item(_ issue: HealthSummary.Issue) -> Item { Item(text: issue.title, fix: issue.fix, files: issue.items) }
        var groups: [Group] = []
        let problems = summary.issues(.problem, from: .install)
        if !problems.isEmpty { groups.append(Group(title: "Problems", tone: .error, items: problems.map(item))) }
        let warnings = summary.issues(.warning, from: .install)
        if !warnings.isEmpty { groups.append(Group(title: "Warnings", tone: .warn, items: warnings.map(item))) }
        if let rh = doctor.runHealth {
            let tone: Tone = rh.severity == .green ? .ok : rh.severity == .yellow ? .warn : .error
            let items = summary.issues.filter { $0.source == .runs }.map {
                Item(text: $0.title, fix: $0.fix, since: $0.since)
            }
            groups.append(Group(title: rh.severity == .green ? "Scheduled runs: healthy" : "Scheduled runs",
                                tone: tone,
                                items: items.isEmpty ? [Item(text: "No problems with recent runs.")] : items))
        }
        self.groups = groups
    }
}

/// Help ▸ Run Doctor…: runs the engine's doctor and shows every finding in the app.
struct DoctorView: View {
    @ObservedObject var health: EngineHealthService
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openSettings) private var openSettings
    @State private var running = false

    private var presentation: DoctorPresentation {
        DoctorPresentation(state: health.state, doctor: health.doctor, lastError: health.lastError)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    ForEach(presentation.groups, id: \.title) { group in
                        groupView(group)
                    }
                    if presentation.groups.isEmpty && !running {
                        Text("Nothing to report.").font(DS.sans(13)).foregroundStyle(DS.Ink.p3)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(20)
            }
            Divider()
            footer
        }
        .frame(minWidth: 520, idealWidth: 620, minHeight: 360, idealHeight: 520)
        .background(DS.Paper.base)
        .task { await run() }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Circle().fill(color(presentation.tone)).frame(width: 10, height: 10).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(running ? "Running doctor…" : presentation.headline)
                    .font(DS.sans(15, weight: .semibold)).foregroundStyle(DS.Ink.p1)
                Text(health.lastChecked.map { "Checked \($0.formatted(date: .omitted, time: .shortened))" } ?? "Not checked yet")
                    .font(DS.sans(11)).foregroundStyle(DS.Ink.p3)
            }
            .accessibilityElement(children: .combine)
            Spacer()
            if running { ProgressView().controlSize(.small) }
            Button("Run Again") { Task { await run() } }
                .disabled(running || !presentation.canRun)
                .help(presentation.canRun ? "Run the doctor again" : "Set up or repair the engine in Settings first")
        }
        .padding(20)
    }

    private var footer: some View {
        HStack {
            Button("Engine Settings…") { HelpActions.showEngineSettings(openSettings) }
            Spacer()
            Button("Export Diagnostics…") { openWindow(id: HelpWindow.diagnostics) }
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
    }

    private func groupView(_ group: DoctorPresentation.Group) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(group.title.uppercased())
                .font(DS.sans(11, weight: .semibold)).foregroundStyle(color(group.tone))
                .accessibilityAddTraits(.isHeader)
            ForEach(Array(group.items.enumerated()), id: \.offset) { _, item in
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.text).font(DS.sans(13)).foregroundStyle(DS.Ink.p1).textSelection(.enabled)
                    if let fix = item.fix {
                        Text("How to fix: \(fix)").font(DS.sans(12)).foregroundStyle(DS.Ink.p2).textSelection(.enabled)
                    }
                    if !item.files.isEmpty {
                        IssueFilesDisclosure(files: item.files)
                    }
                }
            }
        }
    }

    private func color(_ tone: DoctorPresentation.Tone) -> Color {
        switch tone {
        case .ok: return DS.Status.ok
        case .warn: return DS.Status.warn
        case .error: return DS.Status.err
        case .neutral: return DS.Ink.p3
        }
    }

    private func run() async {
        guard !running else { return }
        running = true
        await health.refresh()
        running = false
    }
}
