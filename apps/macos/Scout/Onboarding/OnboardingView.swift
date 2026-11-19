import SwiftUI
import AppKit

/// Settings ▸ Engine's "Set up…" / "Repair…" (Ruling 41): the same flow the
/// window's gate shows, in a sheet with a Close button. Closing only hides
/// it — the flow (and any install it is running) carries on in `AppState`.
struct OnboardingSheet: View {
    @ObservedObject var model: OnboardingViewModel
    let close: () -> Void

    var body: some View {
        OnboardingView(model: model)
            .overlay(alignment: .topTrailing) {
                Button("Close", action: close)
                    .keyboardShortcut(.cancelAction)
                    .buttonStyle(.ds(.secondary, size: .small))
                    .padding(16)
            }
            .frame(width: 720, height: 600)
    }
}

/// First-launch setup (spec §5). Thin: every decision lives in
/// `OnboardingViewModel`; this file only lays out the current step.
struct OnboardingView: View {
    @ObservedObject var model: OnboardingViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            stepIndicator.padding(.bottom, 20)
            ScrollView { content.frame(maxWidth: 640, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading) }
            if let err = model.lastError {
                Text(err).font(DS.font(.label)).foregroundStyle(DS.Status.err).textSelection(.enabled).padding(.top, 10)
            }
            footer
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Paper.base)
        .task { await model.start() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(model.flowTitle).font(DS.font(.hero, .serif, weight: .medium)).foregroundStyle(DS.Ink.p1)
                .accessibilityAddTraits(.isHeader)
            Text(subtitle).font(DS.font(.label)).foregroundStyle(DS.Ink.p3)
        }.padding(.bottom, 14)
    }

    private var subtitle: String {
        switch model.step {
        case .welcome:
            return model.isRepair
                ? "Scout's engine isn't working, so setup runs again to repair it."
                : "Scout sets up everything it needs. You only need Claude Code."
        case .prerequisites: return "Claude Code must already be on this Mac."
        case .engine: return "Installing the Scout engine."
        case .identity: return "Your name and email go in Scout's commit messages and notes."
        case .connectors: return "Found in Claude Code. Untick anything Scout shouldn't read."
        case .vault: return "Creating your Scout folder and scheduling the runs."
        case .ready: return "Scout is set up."
        }
    }

    /// "Step 3 of 7 · Install the engine" over the bar, so the steps have
    /// names and a count (audit #89).
    private var stepIndicator: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Step \(model.step.number) of \(OnboardingViewModel.Step.count) · \(model.step.title)")
                .font(DS.font(.caption, weight: .medium)).foregroundStyle(DS.Ink.p3)
            HStack(spacing: 6) {
                ForEach(OnboardingViewModel.Step.allCases, id: \.rawValue) { s in
                    Capsule().fill(s.rawValue <= model.step.rawValue ? DS.Accent.fill : DS.Paper.sunk).frame(height: 4)
                        .help(s.title)
                }
            }
            .accessibilityHidden(true)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var content: some View {
        switch model.step {
        case .welcome: welcome
        case .prerequisites: prerequisites
        case .engine: progressList([.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode])
        case .identity: identity
        case .connectors: connectors
        case .vault: vault
        case .ready: ready
        }
    }

    /// The chosen folder sits beside the progress, with Change… back to the
    /// step that picks it — a refused vault is fixed there.
    private var vault: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Text("Scout folder").font(DS.font(.label, weight: .medium)).foregroundStyle(DS.Ink.p2)
                Text(model.vaultURL.path).font(DS.font(.caption, .mono)).foregroundStyle(DS.Ink.p1).lineLimit(1).truncationMode(.middle)
                if model.canChangeVault {
                    Button("Change…") { model.changeVault() }
                        .buttonStyle(.ds(.link, size: .small))
                }
            }
            progressList([.bootstrapVault, .verify])
        }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 12) {
            bullet("Scout reads your work tools on a schedule and writes a short list of what needs your attention each day.")
            bullet("It keeps everything, your action items and notes, in the Scout folder below, on this Mac.")
            DisclosureGroup("What gets installed?") {
                VStack(alignment: .leading, spacing: 6) {
                    bullet("The Scout engine, a Claude Code plugin, in ~/.local/share/scout.")
                    bullet("A private Python environment built with uv. Nothing system-wide changes.")
                    bullet("Claude Code learns the /scout-* commands, and macOS (launchd) starts the scheduled runs.")
                }
                .padding(.top, 4)
            }
            .font(DS.font(.body))
            HStack(alignment: .bottom, spacing: 8) {
                field("Scout folder", text: $model.vaultPath, placeholder: "~/Scout")
                Button("Choose…", action: chooseFolder)
                    .buttonStyle(.dsSecondary)
                    .help("Pick the folder Scout keeps your action items and notes in")
            }
            .glossaryHelp(.scoutFolder)
            if !model.vaultPathIsValid {
                Text("Use a full path that starts with / or ~, such as ~/Scout.").font(DS.font(.caption)).foregroundStyle(DS.Status.warn)
            }
        }
    }

    private var prerequisites: some View {
        let p = model.prerequisites
        let claudeOK = p?.canInstallEngine == true
        let signedIn = p?.auth == .signedIn
        let gitOK = p.map { $0.git != .missing } ?? false
        return VStack(alignment: .leading, spacing: 10) {
            prereqRow("Claude Code", ok: claudeOK, detail: claudeDetail(p?.claude),
                      actionTitle: claudeOK ? nil : "Install in Terminal…", action: installClaudeCode)
            prereqRow("Signed in to Claude", ok: signedIn,
                      detail: signedIn ? "Signed in" : "Scheduled runs need a signed-in Claude Code. You can finish setup first.",
                      actionTitle: signedIn || !claudeOK ? nil : "Sign in…", action: signIn)
            prereqRow("Command Line Tools (git)", ok: gitOK,
                      detail: gitOK ? "Present" : "Your Scout folder is a git repository. Apple will prompt to install the tools.",
                      actionTitle: gitOK || p == nil ? nil : "Install…", action: installCommandLineTools)
            Button("Re-check", action: recheck)
                .buttonStyle(.ds(.secondary, size: .small))
        }
        // One loop per appearance: the model supersedes an older poll, stops
        // when the step changes, and SwiftUI cancels it when this disappears.
        .task { await model.pollPrerequisites() }
    }

    /// The folder picker behind the Scout folder field (audit #90). The
    /// field stays for typing a path; this is the usual Mac way to pick one.
    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Use This Folder"
        panel.message = "Choose a new, empty folder for Scout's action items and notes, or make one with New Folder. Scout adds its own files, so don't pick a folder you already use, such as Documents."
        panel.directoryURL = model.vaultURL.deletingLastPathComponent()
        if panel.runModal() == .OK, let url = panel.url { model.chooseVault(url) }
    }

    private func installClaudeCode() { model.installClaudeCode() }
    private func signIn() { model.signIn() }
    private func installCommandLineTools() { Task { await model.installCommandLineTools() } }
    private func recheck() { Task { await model.recheckPrerequisites() } }

    private func claudeDetail(_ status: ClaudeStatus?) -> String {
        switch status {
        case .installed(_, let version)?: return "Installed" + (version.map { " (\($0))" } ?? "")
        case .missing?: return "Not found. Install it in Terminal, then re-check."
        case nil: return "Checking…"
        }
    }

    private var identity: some View {
        VStack(alignment: .leading, spacing: 12) {
            field("Instance name", text: $model.identity.instanceName, placeholder: "Scout")
            field("Your name", text: $model.identity.userName, placeholder: "Alex")
            field("Email", text: $model.identity.userEmail, placeholder: "alex@example.com")
            timezonePicker
            spendingLimits
        }
    }

    /// A picker, not free text: a mistyped zone name would quietly schedule
    /// every run at the wrong hour (audit #90).
    private var timezonePicker: some View {
        VStack(alignment: .leading, spacing: 6) {
            fieldLabel("Time zone")
            Picker("Time zone", selection: $model.identity.timezone) {
                ForEach(OnboardingViewModel.timezoneChoices(including: model.identity.timezone), id: \.self) { id in
                    Text(id.replacingOccurrences(of: "_", with: " ")).tag(id)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .fixedSize()
            .accessibilityLabel("Time zone")
        }
    }

    /// The two budget numbers, named as Settings ▸ Budget names them, on
    /// the step that asks about you rather than among the connectors (#90).
    private var spendingLimits: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Spending limits").font(DS.font(.headline, weight: .medium)).foregroundStyle(DS.Ink.p1)
                .padding(.top, 6)
                .accessibilityAddTraits(.isHeader)
            field("Limit per run (USD)", text: $model.identity.maxBudget, placeholder: "5.00")
            helpText("The most one scheduled run may spend before Claude Code stops it.")
            field("Daily budget (USD, optional)", text: $model.dailyBudget, placeholder: "20.00")
            if model.dailyBudgetIsValid {
                helpText("What the scheduled runs may spend in a day. Leave it empty for Scout's default; change it later in Settings ▸ Budget.")
            } else {
                Text("Enter a positive amount like 20 or 12.50, or leave it empty.").font(DS.font(.caption)).foregroundStyle(DS.Status.warn)
            }
        }
    }

    private var connectors: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(model.connectorKeys, id: \.self) { key in
                Toggle(isOn: connectorBinding(key)) {
                    HStack(spacing: 8) {
                        Text(ConnectorDetection.displayNames[key] ?? key).font(DS.font(.body, weight: .medium)).foregroundStyle(DS.Ink.p1)
                        let status = model.detections[key]?.status
                        Text(statusLabel(status)).font(DS.font(.caption)).foregroundStyle(status == .connected ? DS.Status.ok : DS.Ink.p3)
                    }
                }
                .toggleStyle(.checkbox)
            }
            if model.enabledConnectors.contains("slack") { field("Slack user ID", text: $model.identity.userSlackID, placeholder: "U0123456789") }
            if model.enabledConnectors.contains("github") {
                field("GitHub username", text: $model.identity.githubUsername, placeholder: "alex")
                field("GitHub repos to watch (comma-separated)", text: $model.identity.githubRepos, placeholder: "example-org/app,example-org/api")
            }
            helpText("You connect these tools in Claude Code. Connected more since this list was made? Check again.")
            Button("Check again") { Task { await model.detectConnectors() } }
                .buttonStyle(.ds(.secondary, size: .small)).disabled(model.busy)
        }
    }

    private func connectorBinding(_ key: String) -> Binding<Bool> {
        Binding(get: { model.enabledConnectors.contains(key) },
                set: { on in if on { model.enabledConnectors.insert(key) } else { model.enabledConnectors.remove(key) } })
    }

    private var ready: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let note = model.restartNote {
                Text(note).font(DS.font(.body, weight: .medium)).foregroundStyle(DS.Status.warn)
            }
            if let d = model.doctor { readySummary(OnboardingViewModel.readySummary(d)) }
            Button("Run your first briefing now") { Task { await model.runFirstBriefing() } }
                .buttonStyle(.dsSecondary).disabled(model.busy)
            if let status = model.briefingStatus { Text(status).font(DS.font(.label)).foregroundStyle(DS.Status.ok) }
            if model.prerequisites?.auth != .signedIn {
                Text("Sign in to Claude Code before the first scheduled run.").font(DS.font(.caption)).foregroundStyle(DS.Status.warn)
            }
        }
    }

    /// The health check in plain words; the engine's own lines sit behind
    /// Details, selectable for a bug report (audit #88).
    private func readySummary(_ summary: OnboardingViewModel.ReadySummary) -> some View {
        let color: Color = switch summary.tone { case .ok: DS.Status.ok; case .warn: DS.Status.warn; case .err: DS.Status.err }
        let symbol = switch summary.tone {
        case .ok: "checkmark.circle.fill"; case .warn: "exclamationmark.triangle.fill"; case .err: "xmark.octagon.fill"
        }
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: symbol).foregroundStyle(color).accessibilityHidden(true)
                Text(summary.title).font(DS.font(.body, weight: .semibold)).foregroundStyle(color)
            }
            helpText(summary.detail)
            if !summary.details.isEmpty {
                DisclosureGroup("Details") {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(summary.details, id: \.self) { line in
                            Text(line).font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .textSelection(.enabled)
                    .padding(.top, 4)
                }
                .font(DS.font(.label))
            }
        }
        .accessibilityElement(children: .contain)
    }

    private func progressList(_ steps: [InstallStep]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(steps, id: \.rawValue) { s in
                let p = model.progress[s]
                HStack(alignment: .top, spacing: 10) {
                    InstallStepStatusIcon(status: p?.status)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(s.title).font(DS.font(.body, weight: .medium)).foregroundStyle(DS.Ink.p1)
                        if let log = p?.log, !log.isEmpty { InstallStepLog(log: log, failed: p?.status.isFailed == true) }
                    }
                }
                .accessibilityElement(children: .combine)
            }
            if model.canRetry {
                // While a step can be retried, Continue is off, so Retry is
                // the one thing this screen is for. No Return: it installs.
                Button(model.retryTitle, action: retry)
                    .buttonStyle(.dsPrimary)
            }
        }
    }

    private func retry() { Task { await model.retry() } }

    private var footer: some View {
        HStack {
            if model.step != .welcome && model.step != .ready {
                Button("Back") { model.back() }.buttonStyle(.dsSecondary).disabled(model.busy)
            }
            Spacer()
            // A real primary button that looks disabled when it is (audit
            // #87). Return triggers it only where it just moves on: where
            // Continue installs the engine or creates the Scout folder, it
            // says so and takes a click, so Return in a text field can't.
            if model.step == .ready {
                Button("Open Scout") { model.finish() }
                    .buttonStyle(.dsPrimary)
                    .keyboardShortcut(.defaultAction)
            } else {
                Button(model.busy ? "Working…" : model.continueTitle) { Task { await model.continueTapped() } }
                    .buttonStyle(.dsPrimary)
                    .keyboardShortcut(model.continueTakesReturn ? .defaultAction : nil)
                    .disabled(!model.canContinue || model.busy)
            }
        }.padding(.top, 16)
    }

    // MARK: atoms

    private func bullet(_ text: String) -> some View { Text("• " + text).font(DS.font(.body)).foregroundStyle(DS.Ink.p2) }

    private func fieldLabel(_ label: String) -> some View {
        Text(label.uppercased()).font(DS.font(.caption, weight: .medium)).tracking(0.66).foregroundStyle(DS.Ink.p4)
            .accessibilityHidden(true)
    }

    private func helpText(_ text: String) -> some View {
        Text(text).font(DS.font(.caption)).foregroundStyle(DS.Ink.p3).fixedSize(horizontal: false, vertical: true)
    }

    private func field(_ label: String, text: Binding<String>, placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            fieldLabel(label)
            TextField(placeholder, text: text)
                .accessibilityLabel(label).textFieldStyle(.plain).font(DS.font(.body, weight: .medium)).padding(.horizontal, 10).frame(height: 30)
                .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk)
                    .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5)))
        }
    }

    private func prereqRow(_ title: String, ok: Bool, detail: String, actionTitle: String?, action: @escaping () -> Void) -> some View {
        HStack(alignment: .top, spacing: 10) {
            InstallStepStatusIcon(status: ok ? .done : .pending)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(DS.font(.body, weight: .medium)).foregroundStyle(DS.Ink.p1)
                Text(detail).font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
            }
            Spacer()
            if let actionTitle {
                Button(actionTitle, action: action).buttonStyle(.ds(.secondary, size: .small))
            }
        }
    }

    private func statusLabel(_ s: ConnectorDetection.Status?) -> String {
        switch s {
        case .connected?: return "Connected"
        case .needsAuth?: return "Sign in to it in Claude Code"
        case .unavailable?: return "Not found"
        case .unknown?: return "Couldn't tell"
        case nil: return "Not checked"
        }
    }
}

// MARK: - Install progress atoms (shared with EngineUpgradeSheet)

nonisolated extension StepStatus {
    var isFailed: Bool { if case .failed = self { return true }; return false }

    /// The word VoiceOver reads for the step's icon.
    var spokenStatus: String {
        switch self {
        case .pending: return "Not started"
        case .running: return "In progress"
        case .done: return "Done"
        case .skipped: return "Not needed"
        case .failed: return "Failed"
        }
    }
}

/// A step's state as a symbol, with a spinner while it runs, instead of the
/// old ○ ✓ ✗ text glyphs (audit #89).
struct InstallStepStatusIcon: View {
    let status: StepStatus?

    var body: some View {
        Group {
            switch status ?? .pending {
            case .running:
                ProgressView().controlSize(.small).scaleEffect(0.8)
            case .done:
                Image(systemName: "checkmark.circle.fill").foregroundStyle(DS.Status.ok)
            case .skipped:
                Image(systemName: "minus.circle").foregroundStyle(DS.Status.ok)
            case .failed:
                Image(systemName: "xmark.octagon.fill").foregroundStyle(DS.Status.err)
            case .pending:
                Image(systemName: "circle").foregroundStyle(DS.Ink.p4)
            }
        }
        .font(DS.font(.body))
        .frame(width: 16, height: 16)
        .accessibilityElement()
        .accessibilityLabel((status ?? .pending).spokenStatus)
    }
}

/// A step's log: three lines while it runs, all of it (selectable, with
/// Copy) once it failed, since that's when someone needs to read it.
struct InstallStepLog: View {
    let log: String
    let failed: Bool
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(log).font(DS.font(.caption, .mono)).foregroundStyle(failed ? DS.Status.err : DS.Ink.p3)
                .lineLimit(failed ? nil : 3)
                .fixedSize(horizontal: false, vertical: failed)
                .textSelection(.enabled)
            if failed {
                Button(copied ? "Copied" : "Copy details") {
                    let pb = NSPasteboard.general
                    pb.clearContents()
                    pb.setString(log, forType: .string)
                    copied = true
                }
                .buttonStyle(.ds(.link, size: .small))
            }
        }
    }
}
