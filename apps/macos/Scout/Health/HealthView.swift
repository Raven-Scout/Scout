import SwiftUI

/// The Health page: are scheduled runs happening, and did they do what they
/// should? Lists every run-health finding with how to fix it, then the recent
/// runs from the run ledger in plain words — skips and why, timeouts, runs
/// without write protection, and protected files Scout had to put back.
/// Run protection's settings live in Settings ▸ Engine (card C9).
struct HealthView: View {
    @EnvironmentObject var ledgerService: RunLedgerService
    @EnvironmentObject var engineHealth: EngineHealthService
    /// Opens Settings at its Engine section, which holds the run protection controls.
    var openSettings: () -> Void = {}
    /// The vault's `.scout-logs/`, for "Show log" on a run.
    var logsDirectory: URL? = nil

    @State private var filter: HealthPanelModel.Filter = .all
    @State private var everythingElseExpanded = false
    @State private var reviewedExpanded = false

    private var model: HealthPanelModel {
        HealthPanelModel(ledger: ledgerService.ledger, doctor: engineHealth.doctor, filter: filter)
    }

    var body: some View {
        ScrollView {
            // A plain VStack, like ProposalsView (#83): every row is materialised anyway.
            VStack(alignment: .leading, spacing: 18) {
                header
                switch ledgerService.state {
                case .loading:
                    ProgressView("Reading the run history…")
                        .frame(maxWidth: .infinity, alignment: .leading)
                case .unsupported(let message):
                    notice(message, buttonTitle: "Open Settings", action: openSettings)
                case .failed(let message):
                    notice(message, buttonTitle: "Try again") { Task { await ledgerService.refresh() } }
                case .loaded:
                    loaded(model)
                }
            }
            .frame(maxWidth: 920, alignment: .leading)
            .padding(.horizontal, 42)
            .padding(.top, 28)
            .padding(.bottom, 64)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .scrollIndicators(.visible)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Paper.base)
        .task { if ledgerService.ledger == nil { await ledgerService.refresh() } }
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 14) {
                Text("Health")
                    .font(DS.serif(28, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 0)
                if let checked = ledgerService.lastChecked {
                    Text("Checked \(checked.formatted(date: .omitted, time: .shortened))")
                        .font(DS.sans(12))
                        .foregroundStyle(DS.Ink.p3)
                }
                Button("Check now") { Task { await ledgerService.refresh() } }
                    .buttonStyle(.plainHit)
                    .font(DS.sans(12))
            }
            if ledgerService.ledger != nil {
                HStack(spacing: 8) {
                    Circle().fill(color(for: model.severity)).frame(width: 8, height: 8)
                        .accessibilityHidden(true)
                    Text(model.headline).font(DS.sans(14, weight: .medium)).foregroundStyle(DS.Ink.p1)
                }
                Text(model.summary).font(DS.sans(13)).foregroundStyle(DS.Ink.p3)
            }
        }
        .padding(.bottom, 6)
        .overlay(alignment: .bottom) { EditorialRule() }
    }

    // MARK: Loaded

    @ViewBuilder
    private func loaded(_ model: HealthPanelModel) -> some View {
        if model.groups.isEmpty && model.engineIssues.isEmpty {
            Text("Nothing needs fixing.")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p2)
        }
        ForEach(model.groups) { group in
            VStack(alignment: .leading, spacing: 8) {
                sectionLabel(group.group.title)
                ForEach(group.findings, id: \.id) { finding in
                    FindingRow(finding: finding, tone: group.group == .red ? DS.Status.err : DS.Status.warn,
                               vaultDirectory: logsDirectory?.deletingLastPathComponent())
                }
            }
        }
        if !model.engineIssues.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                sectionLabel("Scout's engine")
                ForEach(model.engineIssues) { EngineIssueRow(issue: $0, setReviewed: setReviewed) }
            }
        }
        if !model.reviewedIssues.isEmpty {
            DisclosureGroup(isExpanded: $reviewedExpanded) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("You looked at these and marked them reviewed. Each comes back on its own if what it's about changes.")
                        .font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                    ForEach(model.reviewedIssues) { EngineIssueRow(issue: $0, setReviewed: setReviewed) }
                }
                .padding(.top, 6)
            } label: {
                Text("Reviewed (\(model.reviewedIssues.count))")
                    .font(DS.sans(13, weight: .medium))
                    .foregroundStyle(DS.Ink.p2)
            }
        }
        if !model.everythingElse.isEmpty {
            DisclosureGroup(isExpanded: $everythingElseExpanded) {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(model.everythingElse.enumerated()), id: \.offset) { _, note in
                        Text(note).font(DS.sans(12)).foregroundStyle(DS.Ink.p2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }
                }
                .padding(.top, 6)
            } label: {
                Text("Everything else (\(model.everythingElse.count))")
                    .font(DS.sans(13, weight: .medium))
                    .foregroundStyle(DS.Ink.p2)
            }
        }
        HStack(spacing: 10) {
            Text("Write protection and privacy checks are set in Settings ▸ Engine.")
                .font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
            Button("Open Settings", action: openSettings).buttonStyle(.plainHit).font(DS.sans(12))
        }
        runsSection(model)
    }

    private func runsSection(_ model: HealthPanelModel) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                sectionLabel("Recent runs")
                Spacer(minLength: 0)
                Picker("Show", selection: $filter) {
                    ForEach(HealthPanelModel.Filter.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                .fixedSize()
            }
            if model.runs.isEmpty {
                Text(filter == .all ? "No scheduled runs in the last two weeks." : "No runs need attention.")
                    .font(DS.sans(13)).foregroundStyle(DS.Ink.p3)
            }
            ForEach(model.runs) { line in
                RunLedgerRow(line: line, logsDirectory: logsDirectory)
                Rectangle().fill(DS.Rule.soft).frame(height: 0.5)
            }
            if ledgerService.ledger?.truncated == true {
                Text("Older runs aren't shown.").font(DS.sans(11)).foregroundStyle(DS.Ink.p4)
            }
        }
    }


    private func setReviewed(_ review: HealthSummary.Review, _ reviewed: Bool) async -> String? {
        await engineHealth.setReviewed(review, reviewed: reviewed)
    }

    // MARK: Pieces

    private func sectionLabel(_ text: String) -> some View {
        Text(text.uppercased())
            .font(DS.sans(11, weight: .medium))
            .tracking(0.08 * 11)
            .foregroundStyle(DS.Ink.p3)
            .accessibilityAddTraits(.isHeader)
    }

    private func notice(_ message: String, buttonTitle: String, action: @escaping () -> Void) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(message).font(DS.sans(13)).foregroundStyle(DS.Ink.p2)
            Button(buttonTitle, action: action).buttonStyle(.plainHit).font(DS.sans(13))
        }
    }

    private func color(for severity: DoctorReport.Severity?) -> Color {
        switch severity {
        case .green: return DS.Status.ok
        case .yellow: return DS.Status.warn
        case .red: return DS.Status.err
        case nil: return DS.Ink.p4
        }
    }
}

/// One run-health finding: what's wrong, the run's own error line, and the fix.
struct FindingRow: View {
    let finding: RunHealth.Finding
    let tone: Color
    /// The vault root: `scoutctl runs --json` gives a finding's log vault-relative (`.scout-logs/<name>`).
    var vaultDirectory: URL? = nil

    var body: some View {
        // C4's one way to show a problem: what happened, what to do, and the
        // run's own error line behind "Details" with "Copy details".
        ProblemView(problem: Problem(finding: finding), actions: actions, style: .inline)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 8).fill(DS.Paper.sunk))
            .overlay(alignment: .leading) {
                RoundedRectangle(cornerRadius: 1.5).fill(tone).frame(width: 3).accessibilityHidden(true)
            }
    }

    private var actions: [ProblemAction] {
        guard let url = Self.logURL(finding.log, vault: vaultDirectory) else { return [] }
        return [ProblemAction("Show log") { NSWorkspace.shared.activateFileViewerSelecting([url]) }]
    }

    /// A finding's log on disk: vault-relative from the run ledger, absolute from older engines'
    /// doctor output. nil when it can't be placed inside the vault or isn't there.
    nonisolated static func logURL(_ log: String?, vault: URL?) -> URL? {
        guard let log, !log.isEmpty else { return nil }
        let url: URL
        if log.hasPrefix("/") {
            url = URL(fileURLWithPath: log)
        } else {
            guard let vault, !log.split(separator: "/").contains("..") else { return nil }
            url = vault.appendingPathComponent(log)
        }
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }
}

/// One of the engine's own warnings or problems (the doctor's install checks), with its
/// fix and, for a grouped warning, its files behind a disclosure (UI audit R1). A ranked
/// warning shows its risk and lets the owner mark it reviewed, or show it again (R13).
struct EngineIssueRow: View {
    let issue: HealthSummary.Issue
    /// Marks the issue's warning reviewed (`true`) or shows it again; nil where reviewing isn't offered.
    /// Returns what went wrong, or nil.
    var setReviewed: ((HealthSummary.Review, Bool) async -> String?)? = nil

    @State private var busy = false
    @State private var failure: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let risk = issue.risk {
                    RiskChip(risk: risk)
                }
                Text(issue.title).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            if let reason = issue.riskReason {
                Text("Why: \(reason).").font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let fix = issue.fix {
                (Text("What to do: ").fontWeight(.semibold) + Text(fix))
                    .font(DS.sans(12)).foregroundStyle(DS.Ink.p2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !issue.items.isEmpty {
                IssueFilesDisclosure(files: issue.items)
            }
            if let review = issue.review, let setReviewed {
                reviewBar(review, setReviewed)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(DS.Paper.sunk))
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 1.5).fill(stripe).frame(width: 3).accessibilityHidden(true)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(accessibilityTitle)
    }

    private var isReviewed: Bool { issue.reviewedAt != nil }

    private var stripe: Color {
        if isReviewed { return DS.Ink.p4 }
        return issue.severity == .problem ? DS.Status.err : DS.Status.warn
    }

    private var accessibilityTitle: String {
        let risk = issue.risk.map { "\($0.title), " } ?? ""
        let state = isReviewed ? "Reviewed warning" : issue.severity.singular.capitalized
        return "\(state): \(risk)\(issue.title)"
    }

    private func reviewBar(_ review: HealthSummary.Review,
                           _ setReviewed: @escaping (HealthSummary.Review, Bool) async -> String?) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 10) {
                if let at = issue.reviewedAt {
                    Text("Reviewed \(at.formatted(date: .abbreviated, time: .shortened))")
                        .font(DS.sans(11)).foregroundStyle(DS.Ink.p3)
                }
                Button(busy ? "Saving…" : (isReviewed ? "Show again" : "Mark reviewed")) {
                    busy = true
                    failure = nil
                    Task {
                        failure = await setReviewed(review, !isReviewed)
                        busy = false
                    }
                }
                .buttonStyle(.plainHit)
                .font(DS.sans(12))
                .disabled(busy)
                .help(isReviewed
                      ? "Count this warning again."
                      : "Hide this warning until what it's about changes. Only you can do this; scheduled runs can't.")
            }
            if let failure {
                Label(failure, systemImage: "exclamationmark.triangle.fill")
                    .font(DS.sans(11)).foregroundStyle(DS.Status.warn)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.top, 2)
    }
}

/// "High risk" / "Medium risk" / "Low risk", tinted by how much it matters.
struct RiskChip: View {
    let risk: DoctorReport.Risk

    var body: some View {
        Text(risk.title.uppercased())
            .font(DS.sans(9.5, weight: .semibold))
            .tracking(0.05 * 9.5)
            .foregroundStyle(color)
            .padding(.horizontal, 5)
            .padding(.vertical, 1.5)
            .background(RoundedRectangle(cornerRadius: 3).fill(color.opacity(0.12)))
            .fixedSize()
            .accessibilityHidden(true)   // the row's label says it
    }

    private var color: Color {
        switch risk {
        case .high: return DS.Status.err
        case .medium: return DS.Status.warn
        case .low: return DS.Ink.p3
        }
    }
}

/// One ledger run: when, what, how it ended and why, and anything unusual
/// about its protection.
struct RunLedgerRow: View {
    let line: HealthPanelModel.RunLine
    let logsDirectory: URL?

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .foregroundStyle(color)
                .frame(width: 16)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(line.title).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Ink.p1)
                    Text(line.outcome).font(DS.sans(13)).foregroundStyle(color == DS.Status.ok ? DS.Ink.p2 : color)
                    Spacer(minLength: 0)
                    Text(line.finished.formatted(date: .abbreviated, time: .shortened))
                        .font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
                }
                if let reason = line.reason {
                    Text(reason).font(DS.sans(12)).foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let fix = line.fix {
                    (Text("What to do: ").fontWeight(.semibold) + Text(fix))
                        .font(DS.sans(12)).foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let resets = line.resetsAt {
                    Text("Usage limit resets \(resets.formatted(date: .abbreviated, time: .shortened))")
                        .font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
                }
                ForEach([line.protection, line.integrity].compactMap { $0 }, id: \.self) { concern in
                    Label(concern, systemImage: "shield.lefthalf.filled.slash")
                        .font(DS.sans(12)).foregroundStyle(DS.Status.warn)
                }
                HStack(spacing: 10) {
                    if let length = line.length { meta(length) }
                    if let agent = line.agent { meta(agent) }
                    if let url = logURL {
                        Button("Show log") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                            .buttonStyle(.plainHit)
                            .font(DS.sans(11))
                    }
                }
            }
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }

    private var logURL: URL? {
        guard let log = line.log, let dir = logsDirectory else { return nil }
        let url = dir.appendingPathComponent(log)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    private func meta(_ text: String) -> some View {
        Text(text).font(DS.sans(11)).foregroundStyle(DS.Ink.p3)
    }

    private var icon: String {
        switch line.tone {
        case .ok: return "checkmark.circle"
        case .skipped: return "forward.circle"
        case .warning: return "exclamationmark.shield"
        case .problem: return "exclamationmark.triangle"
        }
    }

    private var color: Color {
        switch line.tone {
        case .ok: return DS.Status.ok
        case .skipped: return DS.Ink.p3
        case .warning: return DS.Status.warn
        case .problem: return DS.Status.err
        }
    }
}
