import AppKit
import SwiftUI

/// The 380 pt detail pane (spec §6.4): one scrolling column — header and facts,
/// actions, reasons, PRs, first prompt, files touched, related sessions.
struct SessionDetailView: View {
    let session: AgentSession?
    let index: SessionIndex?
    let now: Date
    let onSelect: (String) -> Void

    @AppStorage("claudeCLIPath")       private var claudeCLIPath: String = ""
    @AppStorage("cliTerminal")         private var cliTerminal: String = CLITerminal.auto.rawValue
    @AppStorage("customLaunchCommand") private var customLaunchCommand: String = ""
    @State private var confirmingResume = false
    @State private var launchFailure: LaunchFailure?
    /// A resume is finding the folder and opening the terminal.
    @State private var isResuming = false
    /// Set when a resume started somewhere it may not work, keyed by the
    /// session it was about so it never shows under another one.
    @State private var resumeNote: (sessionID: String, text: String)?
    @State private var copiedHandoff = false

    var body: some View {
        Group {
            if let session, let index {
                content(session, index: index)
            } else {
                emptyState
            }
        }
        // A note or "Copied" belongs to the session it was about.
        .onChange(of: session?.id) {
            resumeNote = nil
            copiedHandoff = false
        }
    }

    // MARK: Content

    private func content(_ session: AgentSession, index: SessionIndex) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header(session, index: index)
                actions(session, index: index)
                section("Why") {
                    ForEach(Array(session.stateReasons.enumerated()), id: \.offset) { _, reason in
                        Text("• \(reason)").font(DS.font(.label)).foregroundStyle(DS.Ink.p2)
                    }
                }
                if !session.prs.isEmpty {
                    section(session.prs.count == 1 ? "Pull request" : "Pull requests") {
                        ForEach(session.prs, id: \.self) { prRow($0) }
                    }
                }
                if let prompt = session.transcript?.firstPrompt, !prompt.isEmpty {
                    section("First prompt") {
                        Text(prompt)
                            .font(DS.font(.label))
                            .foregroundStyle(DS.Ink.p2)
                            .lineLimit(14)
                            .textSelection(.enabled)
                    }
                }
                if let files = session.transcript?.filesTouched, !files.isEmpty {
                    section("Files touched") {
                        ForEach(files, id: \.self) { path in
                            Text(path).font(DS.font(.caption, .mono)).foregroundStyle(DS.Ink.p2).lineLimit(1).truncationMode(.middle)
                        }
                    }
                }
                related(session, index: index)
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .id(session.id)
        .confirmationDialog("This session is still open", isPresented: $confirmingResume) {
            Button("Resume in terminal anyway") { resume(session) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("It is still running in Claude. Resuming it in a terminal starts a second copy on the same conversation.")
        }
        .launchFailureAlert("Couldn't resume the session", failure: $launchFailure)
    }

    private func header(_ session: AgentSession, index: SessionIndex) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(session.displayTitle)
                .dsFont(.title, .serif, weight: .medium)
                .foregroundStyle(DS.Ink.p1)
                .textSelection(.enabled)
            HStack(spacing: 8) {
                // A project name is free text and a pill doesn't wrap: shortened
                // in the middle so it can't widen the header past the page.
                let project = SessionsLayout.projectName(for: session.projectKey, in: index)
                DSPill(SessionsHeader.menuTitle(project, limit: 24))
                    .help(project)
                SessionStatePill(state: session.state, isOpen: session.isOpen)
                if session.isOpen {
                    Label("open", systemImage: "circle.fill")
                        .labelStyle(.titleAndIcon)
                        .font(DS.font(.caption))
                        .foregroundStyle(DS.Status.ok)
                }
            }
            VStack(alignment: .leading, spacing: 4) {
                if let branch = session.worktree?.branch { fact("Branch", branch, mono: true) }
                if let name = session.worktree?.name { fact("Worktree", name + (session.worktree?.dirty == true ? " (dirty)" : ""), mono: true) }
                if let model = session.model {
                    fact("Model", [SessionsFormat.shortModel(model), session.effort].compactMap { $0 }.joined(separator: " · "))
                }
                if let created = session.createdAt { fact("Created", created.formatted(date: .abbreviated, time: .shortened)) }
                fact("Last active", SessionsFormat.ago(session.lastActivityAt, now: now))
                if let turns = session.turns { fact("Turns", "\(turns)") }
                if let calls = session.transcript?.toolCalls { fact("Tool calls", "\(calls)") }
            }
        }
    }

    private func actions(_ session: AgentSession, index: SessionIndex) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            actionButtons(session, index: index)
            if let resumeNote, resumeNote.sessionID == session.id {
                Label(resumeNote.text, systemImage: "exclamationmark.triangle")
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Status.warn)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func actionButtons(_ session: AgentSession, index: SessionIndex) -> some View {
        // Wraps rather than widening the detail pane at the narrow window.
        FlowLayout(spacing: 8, lineSpacing: 6) {
            Button {
                if session.isOpen { confirmingResume = true } else { resume(session) }
            } label: {
                if isResuming {
                    HStack(spacing: 4) {
                        ProgressView().controlSize(.mini)
                        Text("Opening…")
                    }
                } else {
                    Label("Resume", systemImage: "terminal")
                }
            }
            .disabled(session.cliSessionID == nil || isResuming)
            .accessibilityLabel(isResuming ? "Opening the terminal" : "Resume")
            .help(session.cliSessionID == nil
                  ? "Claude Code didn't record a session id for this one, so it can't be resumed from a terminal"
                  : "Run claude --resume in your terminal, from the folder the session ran in")

            Button {
                if let url = session.pr?.webURL { NSWorkspace.shared.open(url) }
            } label: {
                Label("Open PR", systemImage: "arrow.triangle.pull")
            }
            .disabled(session.pr?.webURL == nil)

            Button {
                if let path = session.worktree?.path {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
                }
            } label: {
                Label("Reveal", systemImage: "folder")
            }
            .disabled(!worktreeExists(session))
            .help("Show the worktree in Finder")

            Button {
                let text = SessionHandoff.markdown(
                    for: session, projectName: SessionsLayout.projectName(for: session.projectKey, in: index))
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
                copiedHandoff = true
                Task {
                    try? await Task.sleep(for: .seconds(2))
                    copiedHandoff = false
                }
            } label: {
                Label(copiedHandoff ? "Copied" : "Copy handoff",
                      systemImage: copiedHandoff ? "checkmark" : "doc.on.doc")
            }
            .help("Copy a summary of this session to paste into another one")
        }
        .buttonStyle(.ds(.secondary, size: .small))
    }

    private func prRow(_ pr: SessionPR) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text("\(pr.repo)#\(pr.number)").font(DS.font(.caption, .mono, weight: .medium)).foregroundStyle(DS.Ink.p1)
                if pr.stale {
                    Text("cached").font(DS.font(.caption2)).foregroundStyle(DS.Status.warn)
                }
                Spacer(minLength: 0)
                if let url = pr.webURL {
                    Button("Open") { NSWorkspace.shared.open(url) }
                        .buttonStyle(.ds(.link, size: .small))
                }
            }
            Text(prDetail(pr)).font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
        }
    }

    private func prDetail(_ pr: SessionPR) -> String {
        var parts = [pr.state == "unknown" ? "state unknown" : pr.state.lowercased()]
        if let review = pr.reviewLabel, review != "merged", review != "closed" { parts.append(review) }
        if pr.checks != "unknown" && pr.checks != "none" { parts.append("checks \(pr.checks)") }
        if pr.mergeState == "DIRTY" { parts.append("merge conflict") }
        if let fetched = pr.fetchedAt { parts.append("fetched \(SessionsFormat.ago(fetched, now: now))") }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private func related(_ session: AgentSession, index: SessionIndex) -> some View {
        let parent = SessionsLayout.parent(of: session, in: index)
        let children = SessionsLayout.children(of: session, in: index)
        if parent != nil || !children.isEmpty {
            section("Related sessions") {
                if let parent { relatedRow(parent, prefix: "Spawned by") }
                ForEach(children) { relatedRow($0, prefix: "Spawned") }
            }
        }
    }

    private func relatedRow(_ other: AgentSession, prefix: String) -> some View {
        Button { onSelect(other.id) } label: {
            HStack(spacing: 6) {
                Text(prefix).font(DS.font(.caption)).foregroundStyle(DS.Ink.p4)
                SessionStateDot(state: other.state, isOpen: other.isOpen)
                Text(other.displayTitle).font(DS.font(.label)).foregroundStyle(DS.Ink.p1).lineLimit(1)
            }
        }
        .buttonStyle(.plainHit)
    }

    // MARK: Pieces

    private func section<Body: View>(_ title: String, @ViewBuilder body: () -> Body) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased())
                .font(DS.font(.caption2, weight: .medium))
                .tracking(0.6)
                .foregroundStyle(DS.Ink.p4)
            body()
        }
    }

    private func fact(_ label: String, _ value: String, mono: Bool = false) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label).font(DS.font(.caption)).foregroundStyle(DS.Ink.p4).frame(width: 78, alignment: .leading)
            Text(value).font(mono ? DS.font(.caption, .mono) : DS.font(.caption)).foregroundStyle(DS.Ink.p2).lineLimit(1).truncationMode(.middle)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "rectangle.stack.badge.person.crop")
                .font(DS.symbol(36))
                .foregroundStyle(DS.Ink.p4)
            Text(index == nil ? "No session index yet" : "Pick a session")
                .dsFont(.title, .serif, weight: .medium)
                .foregroundStyle(DS.Ink.p2)
            if let index {
                let counts = SessionsLayout.stateCounts(index: index, filter: SessionsFilter(), now: now)
                Text(AgentSessionState.allCases.compactMap { state in
                    counts[state].map { "\($0) \(state.label.lowercased())" }
                }.joined(separator: " · "))
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p3)
                if let generated = index.generatedAt {
                    Text("Index built \(SessionsFormat.ago(generated, now: now))")
                        .font(DS.font(.caption))
                        .foregroundStyle(DS.Ink.p4)
                }
            } else {
                Text("Scout updates this list in the background.")
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Ink.p3)
            }
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }

    // MARK: Actions

    private func worktreeExists(_ session: AgentSession) -> Bool {
        guard let path = session.worktree?.path else { return false }
        return FileManager.default.fileExists(atPath: path)
    }

    /// Off the main thread from the first file read to the terminal opening;
    /// the button shows progress meanwhile and ignores a second click.
    private func resume(_ session: AgentSession) {
        guard !isResuming else { return }
        let config = CLIConfig(
            claudePathOverride: claudeCLIPath,
            terminal: CLITerminal(rawValue: cliTerminal) ?? .auto,
            customCommand: customLaunchCommand
        )
        isResuming = true
        resumeNote = nil
        Task {
            defer { isResuming = false }
            do {
                if let note = try await SessionResume.run(session, config: config) {
                    resumeNote = (session.id, note)
                }
            } catch {
                launchFailure = LaunchFailure(error)
            }
        }
    }
}
