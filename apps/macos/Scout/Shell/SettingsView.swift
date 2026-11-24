import SwiftUI
import ServiceManagement

/// The Settings window (⌘,), Scout's only Settings (UI audit #10: it was
/// also a main-window page). Split into `SettingsTab`s rather than one long
/// scroll (#82); every deep link (`HelpActions.showSettings(at:)`) selects
/// its anchor's tab and scrolls to the section.
///
/// Real preference values (launch-at-login, linear workspace, author name) keep
/// the same `@AppStorage` keys as before, so existing user defaults
/// round-trip without migration. The notification toggles are read by
/// `RunNotificationPolicy`.
struct SettingsView: View {
    @EnvironmentObject var appState: AppState
    @State private var tab: SettingsTab
    /// A deep link's section, scrolled to once its tab is showing.
    @State private var scrollRequest: ScrollRequest?
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled
    @State private var launchAtLoginError: String?
    /// Set while the switch is put back after macOS refused the change.
    @State private var revertingLaunchAtLogin = false
    @AppStorage("launchMinimized") private var launchMinimized: Bool = false
    @AppStorage("linearWorkspace") private var linearWorkspace: String = ""
    /// Blank until the owner (or onboarding) names them; comments are signed
    /// "user" meanwhile (`CommentAuthor.configuredName`).
    @AppStorage(CommentAuthor.defaultsKey) private var authorName: String = ""
    // Read by `RunNotificationPolicy` before each run notification.
    @AppStorage(RunNotificationPolicy.failureKey)   private var notifyOnFailure: Bool = true
    @AppStorage(RunNotificationPolicy.rateLimitKey) private var notifyOnRateLimit: Bool = true
    @AppStorage("claudeCLIPath")       private var claudeCLIPath: String = ""
    @AppStorage("cliTerminal")         private var cliTerminal: String = CLITerminal.auto.rawValue
    @AppStorage("customLaunchCommand") private var customLaunchCommand: String = ""
    @AppStorage("researchQueuePath") private var researchQueuePath: String = ""
    @State private var detectedClaudePath: String?
    /// Settings ▸ Engine's Set up / Repair sheet. It shows `AppState`'s one
    /// onboarding flow, so it closes by itself when that flow finishes.
    @State private var showingSetup = false

    /// `initialTab` lets the layout tests render each tab; the window opens
    /// on General, or on the tab a deep link asked for.
    init(initialTab: SettingsTab = .general) {
        _tab = State(initialValue: initialTab)
    }

    private struct ScrollRequest: Equatable {
        let anchor: String
        let id = UUID()
    }

    private var setupSheetPresented: Binding<Bool> {
        Binding(get: { showingSetup && appState.onboarding != nil }, set: { showingSetup = $0 })
    }

    var body: some View {
        TabView(selection: $tab) {
            page(.general) { generalTab }
            page(.engine) { engineTab }
            page(.runs) { runsTab }
            page(.actionItems) { actionItemsTab }
            page(.advanced) { advancedTab }
            page(.updates) { updatesTab }
        }
        .frame(minWidth: 620, idealWidth: 680, maxWidth: 900, minHeight: 460, idealHeight: 620)
        // The flow finished (or went away): forget the request, so the next
        // flow doesn't pop up on its own (Ruling 69 M3).
        .onChange(of: appState.onboarding == nil) { _, gone in
            if gone { showingSetup = false }
        }
        .sheet(isPresented: setupSheetPresented) {
            if let onboarding = appState.onboarding {
                OnboardingSheet(model: onboarding) { showingSetup = false }
                    .id(ObjectIdentifier(onboarding))
            }
        }
        .task {
            let detected = await Task.detached {
                ClaudeLauncher.resolveClaudePath(override: "")
            }.value
            detectedClaudePath = detected
        }
        // Help ▸ Engine Settings…, every "Open Settings ▸ Engine" button
        // (audit C11, R2) and the panel's "Update available" (#93): open on
        // the tab and at the section asked for.
        .onReceive(NotificationCenter.default.publisher(for: .scoutShowSettingsSection)) { note in
            guard let anchor = note.object as? String else { return }
            show(anchor)
        }
        .onAppear {
            guard let anchor = HelpActions.settingsRequested() else { return }
            show(anchor)
        }
    }

    private func show(_ anchor: String) {
        if let target = SettingsTab(anchor: anchor) { tab = target }
        scrollRequest = ScrollRequest(anchor: anchor)
    }

    // MARK: - Tabs

    @ViewBuilder private var generalTab: some View {
        section(label: "General") {
            SettingsCard {
                SettingsRow(
                    title: "Launch Scout at login",
                    help: "Start the app automatically so it's watching your Scout folder all day."
                ) {
                    SettingsToggle(isOn: $launchAtLogin)
                        .onChange(of: launchAtLogin) { _, newValue in setLaunchAtLogin(newValue) }
                }
                if let launchAtLoginError {
                    Label(launchAtLoginError, systemImage: "exclamationmark.triangle.fill")
                        .font(DS.font(.caption))
                        .foregroundStyle(DS.Status.warn)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.bottom, 12)
                }
                SettingsRow(
                    title: "Start in menu bar",
                    help: "Keep the full window hidden when Scout launches. Use the menu-bar panel until you need it."
                ) {
                    SettingsToggle(isOn: $launchMinimized)
                }
                SettingsRow(
                    title: "Text size",
                    help: "Make the app's text larger. Applies to every window right away."
                ) {
                    TextSizePicker()
                }
            }
        }

        section(label: "Authorship") {
            SettingsCard {
                SettingsField(
                    label: "Your name",
                    help: "Shown next to comments you add to action items, as one word in plain letters: spaces become dashes and other characters are spelled out (Zoë → Zoe, Łaz → Laz)."
                ) {
                    SettingsInput(text: $authorName, placeholder: "Your name")
                    if !CommentAuthor.isNameSet(authorName: authorName) {
                        Label("Scout doesn't know your name yet, so comments you add are signed \u{201C}\(CommentAuthor.fallback)\u{201D}.",
                              systemImage: "person.crop.circle.badge.questionmark")
                            .font(DS.font(.caption))
                            .foregroundStyle(DS.Status.warn)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 4)
                            .accessibilityIdentifier("settings-author-name-unset")
                    }
                }
            }
        }
        .id(SettingsAnchor.authorship)

        section(label: "Notifications") {
            SettingsCard {
                SettingsRow(
                    title: "Notify on failed runs",
                    help: "Notify when a run fails, runs out of time or stops without finishing, or is skipped because Scout couldn't run it safely. Click the notification to open the run."
                ) {
                    SettingsToggle(isOn: $notifyOnFailure)
                }
                SettingsRow(
                    title: "Notify on rate-limit",
                    help: "Notify when a run stops because it hit a Claude usage limit."
                ) {
                    SettingsToggle(isOn: $notifyOnRateLimit)
                }
            }
        }
    }

    @ViewBuilder private var engineTab: some View {
        section(label: "Engine") {
            EngineSettingsSection(
                health: appState.engineHealth,
                bundledVersion: appState.engineRelease?.engine.version,
                isUpdating: appState.isUpgradingEngine,
                unfinishedSwitch: appState.engineSwitchUnfinished,
                vault: appState.scoutDirectory,
                onUpdate: { Task { await appState.runEngineUpgrade() } },
                onSetUp: {
                    if appState.beginOnboarding() != nil { showingSetup = true }
                })
            if !appState.engineHealth.state.gatesTabs {
                RunProtectionSection(service: appState.runProtection, vault: appState.scoutDirectory)
            }
        }
        .id(SettingsAnchor.engine)

        // Labels itself, and shows only once the engine reports an agent copy (#550).
        if !appState.engineHealth.state.gatesTabs {
            AgentCopiesSection(service: appState.delegateStatus, review: appState.inboxReview)
        }
    }

    @ViewBuilder private var runsTab: some View {
        section(label: "Budget") {
            BudgetSettingsSection()
                .environmentObject(appState.budgetSettingsService)
        }

        section(label: "Model") {
            ModelSettingsSection()
                .environmentObject(appState.modelSettingsService)
        }
    }

    @ViewBuilder private var actionItemsTab: some View {
        section(label: "Action Items") {
            ActionItemsDisplaySection()
                .environmentObject(appState.displayProfileService)
        }

        section(label: "Linear") {
            SettingsCard {
                SettingsField(
                    label: "Workspace",
                    help: "Used to build Linear URLs when you click a `[[PROJ-123]]` wikilink or deep link in an action item. Leave blank to open linear.app without a workspace."
                ) {
                    SettingsInput(text: $linearWorkspace, placeholder: "e.g. acme-co")
                }
            }
        }
    }

    @ViewBuilder private var advancedTab: some View {
        section(label: "Claude Code") {
            SettingsCard {
                SettingsField(
                    label: "Claude program",
                    help: "The `claude` program Scout launches. Use Default finds it by itself (`~/.local/bin`, Homebrew, then your login shell)."
                ) {
                    SettingsPathPicker(path: $claudeCLIPath,
                                       defaultLabel: detectedClaudePath ?? "Found automatically",
                                       kind: .executable,
                                       panelMessage: "Choose the claude program",
                                       defaultURL: detectedClaudePath.map { URL(fileURLWithPath: $0) })
                }
                SettingsRow(
                    title: "Open Claude Code in",
                    help: "Which terminal the Launch Claude → Claude Code option uses. Auto prefers Ghostty/tmux and falls back to Terminal.app."
                ) {
                    Picker("", selection: $cliTerminal) {
                        ForEach(CLITerminal.allCases) { t in
                            Text(t.displayName).tag(t.rawValue)
                        }
                    }
                    .labelsHidden()
                    .labelledBySettingsRow()
                    .pickerStyle(.menu)
                    .fixedSize()
                }
                if cliTerminal == CLITerminal.custom.rawValue {
                    SettingsField(
                        label: "Custom launch command",
                        help: "Shell command run via your login shell. `{cwd}` and `{claude}` are inserted as quoted arguments. Example: `kitty -d {cwd} -e {claude}`."
                    ) {
                        SettingsInput(
                            text: $customLaunchCommand,
                            placeholder: "kitty -d {cwd} -e {claude}")
                        if customLaunchCommand.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            Text("Required — enter a command, or Launch Claude → Claude Code will have nothing to run.")
                                .font(DS.font(.caption))
                                .foregroundStyle(DS.Status.warn)
                                .padding(.top, 4)
                        }
                    }
                }
            }
        }

        section(label: "Folders") {
            SettingsCard {
                folderField("Research queue folder", path: $researchQueuePath,
                            defaultRelative: PerFileTabConfig.research.directoryDefaultRelative, reads: "Research")
            }
        }
    }

    @ViewBuilder private var updatesTab: some View {
        section(label: "Updates") { UpdatesSettingsSection() }
            .id(SettingsAnchor.updates)

        section(label: "About") {
            SettingsCard(padding: 14) {
                VStack(alignment: .leading, spacing: 0) {
                    aboutKV("Version", value: appVersion)
                    aboutKV("Bundle",  value: bundleId)
                }
            }
        }
    }

    /// A folder that defaults to `defaultRelative` inside the Scout folder,
    /// shown as `<Scout folder>/…` since the Scout folder itself can move.
    private func folderField(_ label: String, path: Binding<String>, defaultRelative: String, reads page: String) -> some View {
        let defaultLabel = "<Scout folder>/\(defaultRelative)"
        return SettingsField(
            label: label,
            help: "The folder the \(page) page reads. Use Default reads `\(defaultLabel)`. Takes effect after restarting Scout."
        ) {
            SettingsPathPicker(path: path, defaultLabel: defaultLabel, kind: .folder,
                               panelMessage: "Choose the \(label.lowercased())",
                               defaultURL: appState.scoutDirectory.appendingPathComponent(defaultRelative))
        }
    }

    private func setLaunchAtLogin(_ on: Bool) {
        // The switch was put back after a refusal below: doing nothing keeps
        // that refusal's message on screen.
        if revertingLaunchAtLogin {
            revertingLaunchAtLogin = false
            return
        }
        do {
            if on {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
            launchAtLoginError = nil
        } catch {
            let actual = SMAppService.mainApp.status == .enabled
            if actual != launchAtLogin {
                revertingLaunchAtLogin = true
                launchAtLogin = actual
            }
            launchAtLoginError = on
                ? "macOS didn't let Scout start at login. Check System Settings ▸ General ▸ Login Items."
                : "macOS didn't let Scout stop starting at login. Check System Settings ▸ General ▸ Login Items."
        }
    }

    // MARK: - Atoms

    /// One tab: a scrolling column of sections on the paper backdrop, which
    /// scrolls to a deep link's section once it shows.
    private func page<Content: View>(_ item: SettingsTab, @ViewBuilder _ content: () -> Content) -> some View {
        let sections = content()
        return ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    sections
                }
                .frame(maxWidth: 680)
                .padding(.horizontal, 28)
                .padding(.top, 22)
                .padding(.bottom, 40)
                .frame(maxWidth: .infinity, alignment: .center)
            }
            .onChange(of: scrollRequest) { _, request in scroll(proxy, to: request, on: item) }
            .onAppear { scroll(proxy, to: scrollRequest, on: item) }
        }
        .background(PaperBackdrop())
        .tabItem { Label(item.title, systemImage: item.systemImage) }
        .tag(item)
        .accessibilityIdentifier(item.accessibilityIdentifier)
    }

    private func scroll(_ proxy: ScrollViewProxy, to request: ScrollRequest?, on item: SettingsTab) {
        guard let request, SettingsTab(anchor: request.anchor) == item else { return }
        // After the tab's first layout, so the section has a frame. No
        // scroll animation under Reduce Motion.
        // Then forget it, so a later visit to the tab doesn't scroll again.
        DispatchQueue.main.async {
            withMotion { proxy.scrollTo(request.anchor, anchor: .top) }
            if scrollRequest == request { scrollRequest = nil }
        }
    }

    private func section<Content: View>(label: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(label.uppercased())
                .font(DS.font(.caption2, weight: .medium))
                .tracking(0.08 * 10)
                .foregroundStyle(DS.Ink.p4)
                .padding(.horizontal, 4)
            content()
        }
        .padding(.bottom, 22)
    }

    private func aboutKV(_ key: String, value: String, valueColor: Color = DS.Ink.p1) -> some View {
        HStack {
            Text(key)
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
            Text(value)
                .font(DS.font(.label, .mono, weight: .medium))
                .foregroundStyle(valueColor)
        }
        .padding(.vertical, 5)
    }

    // MARK: - Derived values

    private var appVersion: String {
        let v = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "dev"
        let b = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?"
        #if DEBUG
        // Release builds get a stamped MARKETING_VERSION via scripts/release.sh.
        // Mark dev builds and show the build time so it's obvious which local
        // build is running even when its marketing version matches a release.
        return "\(v) (\(b)) · dev · \(buildTimestamp)"
        #else
        return "\(v) (\(b))"
        #endif
    }

    #if DEBUG
    private var buildTimestamp: String {
        guard let exe = Bundle.main.executableURL,
              let attrs = try? FileManager.default.attributesOfItem(atPath: exe.path),
              let date = attrs[.modificationDate] as? Date else { return "?" }
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd HH:mm"
        return fmt.string(from: date)
    }
    #endif

    private var bundleId: String {
        Bundle.main.bundleIdentifier ?? "com.scout.Scout"
    }
}

/// Settings ▸ General ▸ Text size. macOS has no system text size for apps
/// to follow, so this is Scout's own; see `TextScale`.
private struct TextSizePicker: View {
    @Bindable private var scale = TextScale.shared

    var body: some View {
        Picker("Text size", selection: $scale.size) {
            ForEach(TextScale.Size.allCases) { Text($0.title).tag($0) }
        }
        .labelsHidden()
        .labelledBySettingsRow()
        .pickerStyle(.menu)
        .fixedSize()
        .accessibilityIdentifier("settings-text-size")
    }
}
