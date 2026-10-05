import SwiftUI

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
                Text(err).font(DS.sans(12)).foregroundStyle(DS.Status.err).textSelection(.enabled).padding(.top, 10)
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
            Text("Set up Scout").font(DS.serif(24, weight: .medium)).foregroundStyle(DS.Ink.p1)
            Text(subtitle).font(DS.sans(12.5)).foregroundStyle(DS.Ink.p3)
        }.padding(.bottom, 14)
    }

    private var subtitle: String {
        switch model.step {
        case .welcome: return "Scout.app installs and manages everything it needs — except Claude Code itself."
        case .prerequisites: return "Claude Code must already be on this Mac."
        case .engine: return "Installing the Scout engine."
        case .identity: return "Used in commit messages and the knowledge base."
        case .connectors: return "Detected from Claude Code. Toggle anything the detection got wrong."
        case .vault: return "Creating your vault and scheduling the sessions."
        case .ready: return "Scout is set up."
        }
    }

    private var stepIndicator: some View {
        HStack(spacing: 6) {
            ForEach(OnboardingViewModel.Step.allCases, id: \.rawValue) { s in
                Capsule().fill(s.rawValue <= model.step.rawValue ? DS.Accent.fill : DS.Paper.sunk).frame(height: 4)
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch model.step {
        case .welcome: welcome
        case .prerequisites: prerequisites
        case .engine: progressList([.ensureUv, .unpackEngine, .buildVenv, .registerWithClaudeCode])
        case .identity: identity
        case .connectors: connectors
        case .vault: progressList([.bootstrapVault, .verify])
        case .ready: ready
        }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 12) {
            bullet("The Scout engine (a Claude Code plugin) goes to ~/.local/share/scout.")
            bullet("A private Python environment is built with uv — nothing system-wide changes.")
            bullet("Claude Code learns the /scout-* commands; scheduled sessions run via launchd.")
            bullet("Your knowledge base and action items live in the vault folder below.")
            field("Vault folder", text: $model.vaultPath, placeholder: "~/Scout")
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
                      detail: gitOK ? "Present" : "Your vault is a git repository. Apple will prompt to install the tools.",
                      actionTitle: gitOK || p == nil ? nil : "Install…", action: installCommandLineTools)
            Button("Re-check", action: recheck)
                .buttonStyle(.plainHit).font(DS.sans(12, weight: .medium)).foregroundStyle(DS.Accent.ink)
        }
        // One loop per appearance: the model supersedes an older poll, stops
        // when the step changes, and SwiftUI cancels it when this disappears.
        .task { await model.pollPrerequisites() }
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
            field("Timezone", text: $model.identity.timezone, placeholder: TimeZone.current.identifier)
        }
    }

    private var connectors: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(model.connectorKeys, id: \.self) { key in
                Toggle(isOn: connectorBinding(key)) {
                    HStack(spacing: 8) {
                        Text(ConnectorDetection.displayNames[key] ?? key).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Ink.p1)
                        let status = model.detections[key]?.status
                        Text(statusLabel(status)).font(DS.sans(11.5)).foregroundStyle(status == .connected ? DS.Status.ok : DS.Ink.p3)
                    }
                }
                .toggleStyle(.checkbox)
            }
            if model.enabledConnectors.contains("slack") { field("Slack user ID", text: $model.identity.userSlackID, placeholder: "U0123456789") }
            if model.enabledConnectors.contains("github") {
                field("GitHub username", text: $model.identity.githubUsername, placeholder: "alex")
                field("GitHub repos to watch (comma-separated)", text: $model.identity.githubRepos, placeholder: "example-org/app,example-org/api")
            }
            field("Per-session budget (USD)", text: $model.identity.maxBudget, placeholder: "5.00")
            field("Daily budget (USD, optional)", text: $model.dailyBudget, placeholder: "20.00")
            if !model.dailyBudgetIsValid {
                Text("Enter a positive amount like 20 or 12.50, or leave it empty.").font(DS.sans(11.5)).foregroundStyle(DS.Status.warn)
            }
            Text("Connectors are authorized in Claude Code (claude mcp list). Re-detect after connecting more.")
                .font(DS.sans(11.5)).foregroundStyle(DS.Ink.p3)
            Button("Re-detect") { Task { await model.detectConnectors() } }
                .buttonStyle(.plainHit).font(DS.sans(12, weight: .medium)).foregroundStyle(DS.Accent.ink).disabled(model.busy)
        }
    }

    private func connectorBinding(_ key: String) -> Binding<Bool> {
        Binding(get: { model.enabledConnectors.contains(key) },
                set: { on in if on { model.enabledConnectors.insert(key) } else { model.enabledConnectors.remove(key) } })
    }

    private var ready: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let note = model.restartNote {
                Text(note).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Status.warn)
            }
            if let d = model.doctor {
                Text("Health: \(d.severity.rawValue)").font(DS.sans(13, weight: .medium))
                    .foregroundStyle(d.severity == .red ? DS.Status.err : d.severity == .yellow ? DS.Status.warn : DS.Status.ok)
                ForEach(d.errors + d.warnings, id: \.self) { Text($0).font(DS.mono(11)).foregroundStyle(DS.Ink.p3) }
            }
            Button("Run your first briefing now") { Task { await model.runFirstBriefing() } }
                .buttonStyle(.plainHit).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Accent.ink).disabled(model.busy)
            if let status = model.briefingStatus { Text(status).font(DS.sans(12)).foregroundStyle(DS.Status.ok) }
            if model.prerequisites?.auth != .signedIn {
                Text("Sign in to Claude Code before the first scheduled run.").font(DS.sans(11.5)).foregroundStyle(DS.Status.warn)
            }
        }
    }

    private func progressList(_ steps: [InstallStep]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(steps, id: \.rawValue) { s in
                let p = model.progress[s]
                HStack(alignment: .top, spacing: 10) {
                    Text(glyph(p?.status)).font(DS.mono(13)).foregroundStyle(glyphColor(p?.status)).frame(width: 16)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(s.title).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Ink.p1)
                        if let log = p?.log, !log.isEmpty { Text(log).font(DS.mono(11)).foregroundStyle(DS.Ink.p3).lineLimit(3) }
                    }
                }
            }
            // Only while this step is unfinished: a daily-budget warning after a
            // successful bootstrap must not offer to re-run setup.
            if model.lastError != nil, !model.canContinue, !model.busy {
                Button("Retry") { Task { await retry() } }
                    .buttonStyle(.plainHit).font(DS.sans(12, weight: .medium)).foregroundStyle(DS.Accent.ink)
            }
        }
    }

    private func retry() async {
        if model.step == .engine { await model.installEngine() } else { await model.createVault() }
    }

    private var footer: some View {
        HStack {
            if model.step != .welcome && model.step != .ready {
                Button("Back") { model.back() }.buttonStyle(.plainHit).font(DS.sans(13)).foregroundStyle(DS.Ink.p2).disabled(model.busy)
            }
            Spacer()
            if model.step == .ready {
                Button("Open Scout") { model.finish() }.buttonStyle(.plainHit).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Accent.ink)
            } else {
                Button(model.busy ? "Working…" : "Continue") { Task { await model.continueTapped() } }
                    .buttonStyle(.plainHit).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Accent.ink)
                    .disabled(!model.canContinue || model.busy)
            }
        }.padding(.top, 16)
    }

    // MARK: atoms

    private func bullet(_ text: String) -> some View { Text("• " + text).font(DS.sans(13)).foregroundStyle(DS.Ink.p2) }

    private func field(_ label: String, text: Binding<String>, placeholder: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label.uppercased()).font(DS.sans(11, weight: .medium)).tracking(0.66).foregroundStyle(DS.Ink.p4)
            TextField(placeholder, text: text).textFieldStyle(.plain).font(DS.sans(13, weight: .medium)).padding(.horizontal, 10).frame(height: 30)
                .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk)
                    .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5)))
        }
    }

    private func prereqRow(_ title: String, ok: Bool, detail: String, actionTitle: String?, action: @escaping () -> Void) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(ok ? "✓" : "○").font(DS.mono(13)).foregroundStyle(ok ? DS.Status.ok : DS.Ink.p3).frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Ink.p1)
                Text(detail).font(DS.sans(11.5)).foregroundStyle(DS.Ink.p3)
            }
            Spacer()
            if let actionTitle {
                Button(actionTitle, action: action).buttonStyle(.plainHit).font(DS.sans(12, weight: .medium)).foregroundStyle(DS.Accent.ink)
            }
        }
    }

    private func glyph(_ s: StepStatus?) -> String {
        switch s { case .done?: return "✓"; case .skipped?: return "–"; case .running?: return "…"; case .failed?: return "✗"; default: return "○" }
    }

    private func glyphColor(_ s: StepStatus?) -> Color {
        switch s { case .done?, .skipped?: return DS.Status.ok; case .failed?: return DS.Status.err; default: return DS.Ink.p3 }
    }

    private func statusLabel(_ s: ConnectorDetection.Status?) -> String {
        switch s {
        case .connected?: return "connected"
        case .needsAuth?: return "needs authentication in Claude Code"
        case .unavailable?: return "not found"
        case .unknown?: return "could not detect"
        case nil: return "not detected"
        }
    }
}
