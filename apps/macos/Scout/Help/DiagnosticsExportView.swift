import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// Help ▸ Export Diagnostics…: builds the bundle, shows the owner exactly what
/// it holds, and saves only when they say so (app audit C11).
struct DiagnosticsExportView: View {
    @EnvironmentObject private var appState: AppState
    @State private var bundle: DiagnosticsBundle?
    @State private var savedTo: URL?
    @State private var saveError: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Export Diagnostics").font(DS.font(.headline, weight: .semibold)).foregroundStyle(DS.Ink.p1)
                Text("Check what's below, then save it and attach the file to a bug report. Nothing is sent anywhere. Scout leaves out note text, note and folder names, secrets, tokens, transcripts and run logs.")
                    .font(DS.font(.label)).foregroundStyle(DS.Ink.p2)
                    .fixedSize(horizontal: false, vertical: true)
                DiagnosticsReviewNotice()
            }
            .padding(20)
            Divider()
            if let bundle {
                HStack(alignment: .top, spacing: 0) {
                    contents(bundle).frame(width: 220)
                    Divider()
                    ScrollView {
                        Text(bundle.text)
                            .font(DS.font(.caption, .mono)).foregroundStyle(DS.Ink.p1)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(16)
                    }
                    .accessibilityLabel("Diagnostics text")
                }
            } else {
                VStack(spacing: 10) {
                    ProgressView()
                    Text("Collecting diagnostics…").font(DS.font(.label)).foregroundStyle(DS.Ink.p3)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider()
            footer
        }
        .frame(minWidth: 760, idealWidth: 860, minHeight: 480, idealHeight: 620)
        .background(DS.Paper.base)
        .task { await collect() }
    }

    private func contents(_ bundle: DiagnosticsBundle) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("WHAT'S INCLUDED").font(DS.font(.caption, weight: .semibold)).foregroundStyle(DS.Ink.p3)
                    .accessibilityAddTraits(.isHeader)
                ForEach(bundle.sections) { section in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(section.title).font(DS.font(.label, weight: .medium)).foregroundStyle(DS.Ink.p1)
                        Text(section.about).font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            .padding(16)
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            if let savedTo {
                Text("Saved \(savedTo.lastPathComponent)").font(DS.font(.label)).foregroundStyle(DS.Status.ok)
                Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([savedTo]) }
                    .buttonStyle(.dsLink)
            } else if let saveError {
                Text(saveError).font(DS.font(.label)).foregroundStyle(DS.Status.err).lineLimit(2)
            } else {
                Text(DiagnosticsReviewNotice.short).font(DS.font(.label)).foregroundStyle(DS.Status.warn)
            }
            Spacer()
            Button("Collect Again") { Task { await collect() } }.disabled(bundle == nil)
                .buttonStyle(.dsSecondary)
            Button("Copy") {
                guard let bundle else { return }
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(bundle.text, forType: .string)
            }
            .buttonStyle(.dsSecondary)
            .disabled(bundle == nil)
            Button("Save…") { save() }
                .buttonStyle(.dsPrimary)
                .keyboardShortcut(.defaultAction)
                .disabled(bundle == nil)
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
    }

    private func collect() async {
        bundle = nil
        savedTo = nil
        saveError = nil
        bundle = await appState.collectDiagnostics()
    }

    private func save() {
        guard let bundle else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = bundle.suggestedFileName
        panel.allowedContentTypes = [.plainText]
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            // The exact text shown above, nothing more.
            try bundle.text.write(to: url, atomically: true, encoding: .utf8)
            savedTo = url
            saveError = nil
        } catch {
            saveError = "Couldn't save: \(error.localizedDescription)"
        }
    }
}

extension AppState {
    /// A diagnostics bundle for this vault and engine (Help ▸ Export Diagnostics…).
    func collectDiagnostics(now: Date = Date()) async -> DiagnosticsBundle {
        let vault = scoutDirectory
        let scrubber = await Task.detached { DiagnosticsScrubber.forVault(vault) }.value
        let state = engineHealth.state
        let collector = DiagnosticsCollector(
            app: .current(),
            engineState: state,
            doctor: engineHealth.doctor,
            vault: vault,
            runner: runner,
            environment: ["SCOUT_DATA_DIR": vault.path],
            runs: sessionLogService.runs,
            appLog: { DiagnosticsCollector.appLogLines() },
            installLog: state.isManaged ? engineLayout.installLogURL : nil,
            scrubber: scrubber,
            now: now
        )
        return await collector.collect()
    }
}

/// The reminder that a scrub can't catch everything (app audit C11 review): shown above the
/// preview, with a short form beside Save. Same sentence as the plugin README's FAQ.
struct DiagnosticsReviewNotice: View {
    static let text = "Scout hid the names and paths it could recognise. Read it through before attaching it to a public issue: it may still contain something only you'd recognise."
    static let short = "Read it through before you attach it."

    var body: some View {
        Label {
            Text(Self.text)
                .font(DS.font(.label, weight: .medium)).foregroundStyle(DS.Ink.p1)
                .fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: "eye").foregroundStyle(DS.Status.warn)
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Accent.wash, in: RoundedRectangle(cornerRadius: 6))
        .accessibilityElement(children: .combine)
    }
}
