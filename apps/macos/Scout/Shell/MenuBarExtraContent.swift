import SwiftUI
import AppKit

/// Compact command center shown from Scout's menu-bar icon. Window style is
/// intentional: it supports richer status and schedule rows while keeping the
/// full Scout window optional.
struct MenuBarExtraContent: View {
    @EnvironmentObject var state: AppState
    @EnvironmentObject var updates: UpdateService
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openSettings) private var openSettings
    @Environment(\.dismiss) private var dismiss
    @State private var appeared = false
    @State private var hoveredSlotKey: String?
    /// "Install wake schedule" asks first: it changes the Mac's power settings.
    @State private var confirmingWake = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            statusCard
                .padding(.top, 12)
            upcomingSection
                .padding(.top, 16)
            if let error = state.fireNowError {
                Text(error)
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Status.err)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 10)
            }
            if confirmingWake {
                wakeConfirmation
                    .padding(.top, 10)
            } else if let result = state.wakeScheduleResult {
                wakeResult(result)
                    .padding(.top, 10)
            }
            footer
                .padding(.top, 14)
        }
        .padding(16)
        .frame(width: 340)
        .background(DS.Paper.base)
        .opacity(appeared ? 1 : 0)
        .offset(y: appeared ? 0 : -5)
        .onAppear {
            Task { await state.refreshUrgentActionCount() }
            // Hand over the window opener so a notification click can reopen
            // a closed main window (card C3).
            if state.openMainWindow == nil {
                state.openMainWindow = { [openWindow] in openWindow(id: "main") }
            }
            if reduceMotion {
                appeared = true
            } else {
                withAnimation(.spring(response: 0.3, dampingFraction: 0.88)) {
                    appeared = true
                }
            }
        }
        .onDisappear { appeared = false }
    }

    /// The full header when it fits; otherwise the Open button drops its
    /// word, so the urgent count never truncates to "23 urg…" (UI audit R10).
    private var header: some View {
        ViewThatFits(in: .horizontal) {
            headerRow(compact: false)
            headerRow(compact: true)
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: state.urgentActionCount)
    }

    private func headerRow(compact: Bool) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "binoculars.fill")
                .font(DS.font(.headline, weight: .semibold))
                .foregroundStyle(DS.Accent.ink)
                .frame(width: 30, height: 30)
                .background(Circle().fill(DS.Accent.wash))
            VStack(alignment: .leading, spacing: 1) {
                Text("Scout")
                    .dsFont(.title, .serif, weight: .semibold)
                    .foregroundStyle(DS.Ink.p1)
                Text("Quick control")
                    .font(DS.font(.caption2, weight: .medium))
                    .foregroundStyle(DS.Ink.p4)
            }
            .fixedSize()
            Spacer()
            if state.urgentActionCount > 0 {
                DSPill("\(state.urgentActionCount) urgent", tone: .urgent, symbol: "exclamationmark.circle.fill")
                .transition(.scale(scale: 0.85).combined(with: .opacity))
                .help("Urgent action items waiting for attention")
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(state.urgentActionCount) urgent action items")
            }
            Button {
                openMainWindow()
            } label: {
                if compact {
                    Image(systemName: "macwindow")
                } else {
                    Label("Open", systemImage: "macwindow")
                }
            }
            .buttonStyle(.ds(.secondary, size: .small))
            .fixedSize()
            .help("Open the full Scout window")
            .accessibilityLabel("Open the full Scout window")
        }
    }

    private var statusCard: some View {
        let status = state.status
        return HStack(spacing: 10) {
            Circle()
                .fill(status.tone.color)
                .frame(width: 8, height: 8)
            // Wraps instead of truncating ("Last: Consolidation · 1 ho…"):
            // the panel's window sizes to what its content asks for, so the
            // text has to ask for its second line (UI audit R10).
            VStack(alignment: .leading, spacing: 2) {
                Text(status.title)
                    .font(DS.font(.label, weight: .semibold))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                Text(status.detail)
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(status.title). \(status.detail)")
            if let fix = status.fix {
                Button(fix.title) { perform(fix) }
                    .buttonStyle(.ds(.secondary, size: .small))
                    .fixedSize()
            }
        }
        .editorialCard(padding: 11)
    }

    /// Setup happens in the main window (it shows the setup flow while the
    /// engine isn't set up); a problem the Health page explains opens Health,
    /// as Control Center's button does (UI audit T4); the rest is fixed from
    /// Settings ▸ Engine.
    private func perform(_ fix: ScoutStatus.Fix) {
        switch fix {
        case .setUp: openMainWindow()
        case .openHealth:
            state.requestedSidebar = .health
            openMainWindow()
        case .openSettings: openScoutSettings(at: SettingsAnchor.engine)
        }
    }

    private var upcomingSection: some View {
        // The schedule, observed here: AppState re-publishes none of it.
        Observing(state.scheduleService) { upcomingList }
    }

    private var upcomingList: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("UPCOMING")
                .font(DS.font(.micro, weight: .semibold))
                .tracking(0.8)
                .foregroundStyle(DS.Ink.p4)

            let upcoming = Array(state.scheduleService.upcoming.prefix(5))
            if upcoming.isEmpty {
                Text("No upcoming runs scheduled")
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p3)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 8)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(upcoming.enumerated()), id: \.element.id) { index, run in
                        upcomingRow(run)
                        if index < upcoming.count - 1 {
                            Rectangle().fill(DS.Rule.soft).frame(height: 0.5)
                        }
                    }
                }
                .padding(.horizontal, 10)
                .editorialCard(padding: 0)
            }
        }
    }

    private func upcomingRow(_ run: UpcomingRun) -> some View {
        let isFiring = state.firingSlotKeys.contains(run.slotKey)
        return HStack(spacing: 9) {
            VStack(alignment: .leading, spacing: 2) {
                Text(run.displayName)
                    .font(DS.font(.caption, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Text(run.scheduledAt.formatted(.dateTime.weekday(.abbreviated).hour().minute()))
                    .font(DS.font(.caption2, .mono))
                    .foregroundStyle(DS.Ink.p3)
            }
            Spacer()
            Button {
                Task { await state.fireNow(slotKey: run.slotKey) }
            } label: {
                HStack(spacing: 5) {
                    if isFiring {
                        ProgressView()
                            .controlSize(.mini)
                            .transition(.scale.combined(with: .opacity))
                    }
                    Text(isFiring ? "Starting…" : "Run now")
                }
            }
            .buttonStyle(.borderless)
            .font(DS.font(.caption2, weight: .medium))
            .foregroundStyle(DS.Accent.ink)
            .disabled(isFiring)
            .help(isFiring ? "This run is being started" : "Start \(run.displayName) now")
            .accessibilityLabel(isFiring ? "Starting \(run.displayName)" : "Run \(run.displayName) now")
        }
        .padding(.horizontal, 5)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(hoveredSlotKey == run.slotKey ? DS.Accent.wash.opacity(0.45) : .clear)
        )
        .contentShape(Rectangle())
        .onHover { hovering in
            hoveredSlotKey = hovering ? run.slotKey : nil
        }
        .animation(reduceMotion ? nil : .easeOut(duration: 0.14), value: hoveredSlotKey)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.16), value: isFiring)
    }

    private var footer: some View {
        VStack(spacing: 0) {
            Rectangle().fill(DS.Rule.soft).frame(height: 0.5)
                .padding(.bottom, 8)
            HStack(spacing: 6) {
                footerButton("Open Scout folder", systemImage: "folder") {
                    dismiss()
                    NSWorkspace.shared.open(state.scoutDirectory)
                }
                // Not SettingsLink: the window-style panel is non-activating,
                // so Settings would open behind the frontmost app. Activate
                // first, then open, and dismiss the panel like a menu would.
                footerButton("Open Scout settings", systemImage: "gearshape") {
                    openScoutSettings()
                }
                if updates.appUpdatesEnabled {
                    // Dev builds never update themselves — don't grow a dead item.
                    footerButton("Check for Updates…", systemImage: "arrow.triangle.2.circlepath") {
                        // Sparkle's own window follows; the panel is non-activating,
                        // so front the app and dismiss it like a menu would.
                        dismiss()
                        NSApp.activate(ignoringOtherApps: true)
                        updates.check(.app)
                    }
                    .disabled(!updates.appCheckAvailable)
                }
                if updates.anyUpdateAvailable {
                    footerButton("Update available", systemImage: "arrow.down.circle") {
                        openScoutSettings(at: SettingsAnchor.updates)
                    }
                }
                footerButton("Install wake schedule", systemImage: "alarm") {
                    state.wakeScheduleResult = nil
                    confirmingWake = true
                }
                .disabled(state.installingWakeSchedule)
                Spacer()
                footerButton("Quit Scout", systemImage: "power") {
                    NSApp.terminate(nil)
                }
            }
        }
    }

    private func footerButton(_ title: String, systemImage: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(DS.font(.caption, weight: .medium))
                .frame(width: 26, height: 24)
        }
        .buttonStyle(.borderless)
        .foregroundStyle(DS.Ink.p3)
        .help(title)
        .accessibilityLabel(title)
    }

    private func openMainWindow() {
        dismiss()
        NSApp.activate(ignoringOtherApps: true)
        // Reopens the "main" Window scene — fronts the existing window or
        // recreates a closed one, without looking the window up by its title.
        openWindow(id: "main")
    }

    /// Front the app and open Settings, at `anchor`'s section when given:
    /// Engine for the status card's fix, Updates for "Update available".
    private func openScoutSettings(at anchor: String? = nil) {
        dismiss()
        NSApp.activate(ignoringOtherApps: true)
        if let anchor {
            HelpActions.showSettings(openSettings, at: anchor)
        } else {
            openSettings()
        }
    }

    // MARK: - Wake schedule

    private var wakeConfirmation: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Wake this Mac for scheduled runs?")
                .font(DS.font(.label, weight: .semibold))
                .foregroundStyle(DS.Ink.p1)
            Text("Scout asks macOS to wake the Mac at the time of the earliest weekday run, on that run's days. It only works while the Mac is plugged in, and it replaces any repeating wake or power-on time already set on this Mac.")
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p3)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Spacer()
                Button("Cancel") { confirmingWake = false }
                    .buttonStyle(.ds(.secondary, size: .small))
                    .keyboardShortcut(.cancelAction)
                Button("Install") {
                    confirmingWake = false
                    Task { await state.installWakeSchedule() }
                }
                .buttonStyle(.ds(.primary, size: .small))
            }
        }
        .editorialCard(padding: 11)
    }

    private func wakeResult(_ result: AppState.WakeScheduleResult) -> some View {
        HStack(alignment: .top, spacing: 6) {
            VStack(alignment: .leading, spacing: 6) {
                ProblemView(problem: result.problem, style: .inline)
                if let command = result.sudoCommand {
                    // The command on its own line, selectable and copyable:
                    // Copy details would bring the whole engine output along.
                    Text(command)
                        .font(DS.font(.caption2, .mono))
                        .foregroundStyle(DS.Ink.p1)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(6)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 5).fill(DS.Paper.sunk))
                    Button("Copy command") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(command, forType: .string)
                    }
                    .buttonStyle(.ds(.secondary, size: .small))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button {
                state.wakeScheduleResult = nil
            } label: {
                Image(systemName: "xmark")
                    .font(DS.font(.caption2, weight: .semibold))
                    .frame(width: 20, height: 20)
            }
            .buttonStyle(.borderless)
            .foregroundStyle(DS.Ink.p3)
            .help("Dismiss")
            .accessibilityLabel("Dismiss")
        }
    }
}
