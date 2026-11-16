import SwiftUI

/// The Health page: are scheduled runs happening, and did they do what they
/// should? Lists every run-health finding with how to fix it, then the recent
/// runs from the run ledger in plain words — skips and why, timeouts, runs
/// without write protection, and protected files Scout had to put back.
/// Run protection's settings live in Settings ▸ Engine (card C9).
struct HealthView: View {
    @EnvironmentObject var ledgerService: RunLedgerService
    @EnvironmentObject var engineHealth: EngineHealthService
    /// Opens Settings (Settings ▸ Engine holds the run protection controls).
    var openSettings: () -> Void = {}
    /// The vault's `.scout-logs/`, for "Show log" on a run.
    var logsDirectory: URL? = nil

    @State private var filter: HealthPanelModel.Filter = .all
    @State private var everythingElseExpanded = false

    private var model: HealthPanelModel {
        HealthPanelModel(ledger: ledgerService.ledger, doctorNotes: engineHealth.doctor?.notes ?? [], filter: filter)
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
        if model.groups.isEmpty {
            Text("Nothing needs fixing.")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p2)
        }
        ForEach(model.groups) { group in
            VStack(alignment: .leading, spacing: 8) {
                sectionLabel(group.group.title)
                ForEach(group.findings, id: \.id) { finding in
                    FindingRow(finding: finding, tone: group.group == .red ? DS.Status.err : DS.Status.warn)
                }
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
private struct FindingRow: View {
    let finding: RunHealth.Finding
    let tone: Color

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 1.5).fill(tone).frame(width: 3)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) {
                Text(finding.summary)
                    .font(DS.sans(13, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                if let detail = finding.detail {
                    Text(detail).font(DS.mono(11)).foregroundStyle(DS.Ink.p2).textSelection(.enabled)
                }
                if !finding.fix.isEmpty {
                    (Text("How to fix: ").font(DS.sans(12, weight: .medium)) + Text(finding.fix).font(DS.sans(12)))
                        .foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                if let log = finding.log {
                    Button("Show log") {
                        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: log)])
                    }
                    .buttonStyle(.plainHit)
                    .font(DS.sans(12))
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(DS.Paper.sunk))
        .accessibilityElement(children: .contain)
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
