import SwiftUI

/// Settings ▸ Engine ▸ Run protection (app UI audit, card C9): whether scheduled runs get write
/// protection, whether Scout's privacy checks load, what the last run's file check found, the
/// commit and message broker, and each slot's agent and limits — each problem with its fix.
///
/// The owner's switch for write protection lives here and only here. Turning it off goes through
/// a confirmation that explains the risk; nothing else in the app can reach it (`OwnerConfirmation`).
struct RunProtectionSection: View {
    @ObservedObject var service: RunProtectionService
    /// The vault the engine reports on; problem logs are relative to it.
    var vault: URL?
    /// Testing seam: what "now" means for "checked 3 hours ago".
    var now: () -> Date = Date.init
    @State private var confirmingTurnOff = false

    var body: some View {
        SettingsCard {
            if let status = service.status {
                content(RunProtectionModel(status: status, vault: vault, now: now()))
            } else {
                SettingsRow(title: "Run protection", help: service.lastError ?? "Checking…") {
                    checkButton
                }
            }
        }
        // On its own the card only reads (no process, no write); testing write protection takes a click.
        .task { if service.status == nil && service.work == nil { await service.refresh() } }
        .confirmationDialog(RunProtectionModel.turnOffTitle, isPresented: $confirmingTurnOff, titleVisibility: .visible) {
            Button(RunProtectionModel.turnOffConfirm, role: .destructive) {
                // The one place an OwnerConfirmation is made: the owner pressed Turn Off in this dialog.
                let confirmation = OwnerConfirmation(ownerConfirmedInDialog: ())
                Task { await service.turnSandboxOff(confirmation) }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(RunProtectionModel.turnOffMessage)
        }
    }

    @ViewBuilder
    private func content(_ model: RunProtectionModel) -> some View {
        SettingsRow(title: "Run protection", help: service.lastError ?? "What keeps scheduled runs from going wrong.") {
            HStack(spacing: 10) {
                Text(model.headline)
                    .font(DS.sans(12, weight: .medium))
                    .foregroundStyle(Self.color(model.headlineTone))
                    .multilineTextAlignment(.trailing)
                checkButton
            }
        }
        ForEach(model.problems) { problem in problemRow(problem) }
        ForEach(model.lines) { line in lineRow(line) }
        ForEach(model.slotLines) { line in lineRow(line) }
        if model.offersTurnOff {
            SettingsRow(title: "Turn off write protection",
                        help: "For working around a problem only. You'll be asked to confirm.") {
                Button("Turn off…") { confirmingTurnOff = true }
                    .buttonStyle(.plainHit)
                    .font(DS.sans(12))
                    .disabled(service.work != nil)
                    .accessibilityHint("Asks for confirmation before turning write protection off for scheduled runs")
            }
        } else if model.offersTurnOn {
            SettingsRow(title: "Turn write protection back on", help: "Recommended. Takes effect from the next scheduled run.") {
                Button(service.work == .turningOn ? "Working…" : "Turn on") { Task { await service.turnSandboxOn() } }
                    .buttonStyle(.plainHit)
                    .font(DS.sans(12))
                    .disabled(service.work != nil)
            }
        }
    }

    private var checkButton: some View {
        Button([.checking, .checkingProtection].contains(service.work) ? "Checking…" : "Check now") {
            Task { await service.checkWriteProtection() }
        }
            .buttonStyle(.plainHit)
            .font(DS.sans(12))
            .disabled(service.work != nil)
    }

    private func lineRow(_ line: RunProtectionModel.Line) -> some View {
        SettingsRow(title: line.title, help: line.help) {
            Text(line.value)
                .font(DS.sans(12, weight: .medium))
                .foregroundStyle(Self.color(line.tone))
                .multilineTextAlignment(.trailing)
        }
        .accessibilityElement(children: .combine)
    }

    private func problemRow(_ problem: RunProtectionModel.ProblemRow) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: problem.tone == .bad ? "exclamationmark.octagon.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(Self.color(problem.tone))
                    .accessibilityLabel(problem.tone == .bad ? "Problem" : "Warning")
                VStack(alignment: .leading, spacing: 4) {
                    Text(problem.summary).font(DS.sans(12.5, weight: .medium)).foregroundStyle(DS.Ink.p1)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(problem.fix).font(DS.sans(11.5)).foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if let action = problem.action {
                    Button(action.buttonTitle) { perform(action) }
                        .buttonStyle(.plainHit)
                        .font(DS.sans(12))
                        .disabled(service.work != nil)
                }
            }
            .padding(.vertical, 12)
            Rectangle().fill(DS.Rule.soft).frame(height: 0.5).opacity(0.6)
        }
    }

    private func perform(_ action: RunProtectionModel.Action) {
        switch action {
        case .turnOnProtection: Task { await service.turnSandboxOn() }
        case .checkProtectionAgain: Task { await service.checkWriteProtection() }
        case .checkPrivacyAgain: Task { await service.checkPrivacyChecks() }
        case .showLog(let url): NSWorkspace.shared.activateFileViewerSelecting([url])
        }
    }

    static func color(_ tone: RunProtectionModel.Tone) -> Color {
        switch tone {
        case .ok: return DS.Status.ok
        case .warn: return DS.Status.warn
        case .bad: return DS.Status.err
        case .neutral: return DS.Ink.p2
        }
    }
}
