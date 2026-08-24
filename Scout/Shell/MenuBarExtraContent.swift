import SwiftUI
import AppKit

/// Compact command center shown from Scout's menu-bar icon. Window style is
/// intentional: it supports richer status and schedule rows while keeping the
/// full Scout window optional.
struct MenuBarExtraContent: View {
    @EnvironmentObject var state: AppState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false
    @State private var hoveredSlotKey: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            statusCard
                .padding(.top, 12)
            upcomingSection
                .padding(.top, 16)
            if let error = state.fireNowError {
                Text(error)
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Status.err)
                    .fixedSize(horizontal: false, vertical: true)
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
            state.refreshUrgentActionCount()
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

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "binoculars.fill")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(DS.Accent.ink)
                .frame(width: 30, height: 30)
                .background(Circle().fill(DS.Accent.wash))
            VStack(alignment: .leading, spacing: 1) {
                Text("Scout")
                    .font(DS.serif(18, weight: .semibold))
                    .foregroundStyle(DS.Ink.p1)
                Text("Quick control")
                    .font(DS.sans(10.5, weight: .medium))
                    .foregroundStyle(DS.Ink.p4)
            }
            Spacer()
            if state.urgentActionCount > 0 {
                HStack(spacing: 4) {
                    Image(systemName: "exclamationmark.circle.fill")
                    Text("\(state.urgentActionCount) urgent")
                }
                .font(DS.sans(10.5, weight: .semibold))
                .foregroundStyle(DS.Priority.urgent)
                .padding(.horizontal, 7)
                .frame(height: 24)
                .background(Capsule().fill(DS.Priority.urgent.opacity(0.1)))
                .transition(.scale(scale: 0.85).combined(with: .opacity))
                .help("Urgent action items waiting for attention")
                .accessibilityLabel("\(state.urgentActionCount) urgent action items")
            }
            Button {
                openMainWindow()
            } label: {
                Label("Open", systemImage: "macwindow")
                    .font(DS.sans(11.5, weight: .medium))
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .help("Open the full Scout window")
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: state.urgentActionCount)
    }

    private var statusCard: some View {
        let status = currentStatus
        return HStack(spacing: 10) {
            Circle()
                .fill(status.color)
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 2) {
                Text(status.title)
                    .font(DS.sans(12.5, weight: .semibold))
                    .foregroundStyle(DS.Ink.p1)
                Text(status.detail)
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(1)
            }
            Spacer()
        }
        .padding(11)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(status.title). \(status.detail)")
    }

    private var upcomingSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("UPCOMING")
                .font(DS.sans(9.5, weight: .semibold))
                .tracking(0.8)
                .foregroundStyle(DS.Ink.p4)

            let upcoming = Array(state.scheduleService.upcoming.prefix(5))
            if upcoming.isEmpty {
                Text("No upcoming runs scheduled")
                    .font(DS.sans(11.5))
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
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(DS.Paper.raised)
                        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                )
            }
        }
    }

    private func upcomingRow(_ run: UpcomingRun) -> some View {
        let isFiring = state.firingSlotKeys.contains(run.slotKey)
        return HStack(spacing: 9) {
            VStack(alignment: .leading, spacing: 2) {
                Text(run.type.displayName)
                    .font(DS.sans(11.5, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                Text(run.scheduledAt.formatted(.dateTime.weekday(.abbreviated).hour().minute()))
                    .font(DS.mono(10.5))
                    .foregroundStyle(DS.Ink.p3)
            }
            Spacer()
            Button {
                Task { await state.fireNow(slotKey: run.slotKey, bypassBudget: false) }
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
            .font(DS.sans(10.5, weight: .medium))
            .foregroundStyle(DS.Accent.ink)
            .disabled(isFiring)
            .help(isFiring ? "This run is being started" : "Start \(run.type.displayName) now")
            .accessibilityLabel(isFiring ? "Starting \(run.type.displayName)" : "Run \(run.type.displayName) now")
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
                    NSWorkspace.shared.open(state.scoutDirectory)
                }
                SettingsLink {
                    Image(systemName: "gearshape")
                        .frame(width: 26, height: 24)
                }
                .buttonStyle(.borderless)
                .foregroundStyle(DS.Ink.p3)
                .help("Open Scout settings")
                .accessibilityLabel("Open Scout settings")
                footerButton("Install wake schedule", systemImage: "alarm") {
                    installWakeSchedule()
                }
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
                .font(.system(size: 11, weight: .medium))
                .frame(width: 26, height: 24)
        }
        .buttonStyle(.borderless)
        .foregroundStyle(DS.Ink.p3)
        .help(title)
        .accessibilityLabel(title)
    }

    private var currentStatus: (title: String, detail: String, color: Color) {
        guard let latest = state.sessionLogService.runs.first else {
            return ("Ready", "No recent runs", DS.Status.ok)
        }
        switch latest.status {
        case .running:
            return ("Running \(latest.displayName)", "Started \(latest.startedAt.formatted(.relative(presentation: .named)))", DS.Accent.fill)
        case .failure, .timeout, .rateLimited:
            return ("Last run needs attention", "\(latest.displayName) · \(latest.status.rawValue)", DS.Status.err)
        case .skippedBudget:
            return ("Last run skipped", "Budget limit reached", DS.Status.warn)
        default:
            return ("Ready", "Last: \(latest.displayName) · \(latest.status.rawValue)", DS.Status.ok)
        }
    }

    private func openMainWindow() {
        NSApp.activate(ignoringOtherApps: true)
        NSApp.windows.first(where: { $0.title == "Scout" })?.makeKeyAndOrderFront(nil)
    }

    private func installWakeSchedule() {
        Task {
            _ = try? await state.runner.run(
                executable: state.scoutctlExecutable,
                arguments: state.scoutctlArgumentsPrefix + ["schedule", "install-wake-schedule"],
                environment: [:],
                workingDirectory: state.scoutDirectory
            )
        }
    }
}
