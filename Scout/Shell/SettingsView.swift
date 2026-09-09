import SwiftUI
import ServiceManagement

/// Editorial Settings — replaces the bare `Form` with five paper-card sections
/// matching the Scout.html design parity bundle: General / Linear / Authorship /
/// Notifications / About.
///
/// Real preference values (launch-at-login, linear workspace, author name) keep
/// the same `@AppStorage` keys as the old form so existing user defaults
/// round-trip without migration. Notification toggles are new and persist
/// alongside.
struct SettingsView: View {
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled
    @AppStorage("launchMinimized") private var launchMinimized: Bool = false
    @AppStorage("linearWorkspace") private var linearWorkspace: String = ""
    @AppStorage("authorName")      private var authorName: String = "user"
    @AppStorage("notifyOnFailure")   private var notifyOnFailure: Bool = true
    @AppStorage("notifyOnRateLimit") private var notifyOnRateLimit: Bool = true
    @AppStorage("claudeCLIPath")       private var claudeCLIPath: String = ""
    @AppStorage("cliTerminal")         private var cliTerminal: String = CLITerminal.auto.rawValue
    @AppStorage("customLaunchCommand") private var customLaunchCommand: String = ""
    @AppStorage("dreamingProposalsPath") private var dreamingProposalsPath: String = ""
    @AppStorage("wishlistPath")          private var wishlistPath: String = ""
    @AppStorage("researchQueuePath")     private var researchQueuePath: String = ""
    @State private var detectedClaudePath: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header
                section(label: "General") {
                    SettingsCard {
                        SettingsRow(
                            title: "Launch Scout at login",
                            help: "Start the app automatically so it's watching your Scout instance all day."
                        ) {
                            SettingsToggle(isOn: $launchAtLogin)
                                .onChange(of: launchAtLogin) { _, newValue in
                                    do {
                                        if newValue {
                                            try SMAppService.mainApp.register()
                                        } else {
                                            try SMAppService.mainApp.unregister()
                                        }
                                    } catch {
                                        launchAtLogin = (SMAppService.mainApp.status == .enabled)
                                    }
                                }
                        }
                        SettingsRow(
                            title: "Start in menu bar",
                            help: "Keep the full window hidden when Scout launches. Use the menu-bar panel until you need it."
                        ) {
                            SettingsToggle(isOn: $launchMinimized)
                        }
                        SettingsRow(
                            title: "Scout directory",
                            help: "Read-only. The plugin owns this path; the app reads from it."
                        ) {
                            Text(scoutDirPath)
                                .font(DS.mono(11.5, weight: .medium))
                                .foregroundStyle(DS.Ink.p3)
                                .padding(.horizontal, 9)
                                .padding(.vertical, 5)
                                .background(RoundedRectangle(cornerRadius: 5).fill(DS.Paper.sunk))
                        }
                    }
                }

                section(label: "Claude Code") {
                    SettingsCard {
                        SettingsField(
                            label: "Claude binary path",
                            help: "Absolute path to the `claude` CLI. Leave blank to auto-detect (`~/.local/bin`, Homebrew, then your login shell)."
                        ) {
                            SettingsInput(
                                text: $claudeCLIPath,
                                placeholder: detectedClaudePath ?? "Auto-detect")
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
                                        .font(DS.sans(11.5))
                                        .foregroundStyle(DS.Status.warn)
                                        .padding(.top, 4)
                                }
                            }
                        }
                    }
                }

                section(label: "Proposals") {
                    SettingsCard {
                        SettingsField(
                            label: "Dreaming proposals folder",
                            help: "Folder of per-file dreaming proposals the Proposals tab reads. Leave blank to use `~/Scout/dreaming-proposals`. Takes effect after restarting Scout."
                        ) {
                            SettingsInput(
                                text: $dreamingProposalsPath,
                                placeholder: "~/Scout/dreaming-proposals")
                        }
                    }
                }

                section(label: "Wishlist & Research") {
                    SettingsCard {
                        SettingsField(
                            label: "Wishlist folder",
                            help: "Per-file wishlist items the Wishlist tab reads. Blank = `~/Scout/docs/wishlist`. Takes effect after restarting Scout."
                        ) {
                            SettingsInput(
                                text: $wishlistPath,
                                placeholder: "~/Scout/docs/wishlist")
                        }
                        SettingsField(
                            label: "Research queue folder",
                            help: "Per-file research topics the Research tab reads. Blank = `~/Scout/knowledge-base/research-queue`. Takes effect after restarting Scout."
                        ) {
                            SettingsInput(
                                text: $researchQueuePath,
                                placeholder: "~/Scout/knowledge-base/research-queue")
                        }
                    }
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

                section(label: "Authorship") {
                    SettingsCard {
                        SettingsField(
                            label: "Your name",
                            help: "Shown next to comments you add to action items. Default is `user`."
                        ) {
                            SettingsInput(text: $authorName, placeholder: "user")
                        }
                    }
                }

                section(label: "Notifications") {
                    SettingsCard {
                        SettingsRow(
                            title: "Notify on failed runs",
                            help: "Send a system notification when a scheduled run ends in failure or timeout."
                        ) {
                            SettingsToggle(isOn: $notifyOnFailure)
                        }
                        SettingsRow(
                            title: "Notify on rate-limit",
                            help: "Surface 429 / overload signals from the Anthropic API immediately."
                        ) {
                            SettingsToggle(isOn: $notifyOnRateLimit)
                        }
                    }
                }

                section(label: "About") {
                    SettingsCard(padding: 14) {
                        VStack(alignment: .leading, spacing: 0) {
                            aboutKV("Version", value: appVersion)
                            aboutKV("Bundle",  value: bundleId)
                            aboutKV("Plugin",  value: "scout-plugin")
                            aboutKV("Daemon",  value: "healthy", valueColor: DS.Status.ok)
                        }
                    }
                }
            }
            .frame(maxWidth: 720)
            .padding(.horizontal, 32)
            .padding(.top, 28)
            .padding(.bottom, 60)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .task {
            let detected = await Task.detached {
                ClaudeLauncher.resolveClaudePath(override: "")
            }.value
            detectedClaudePath = detected
        }
    }

    // MARK: - Atoms

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Settings")
                .font(DS.serif(24, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
            Text("Preferences for this Scout instance.")
                .font(DS.sans(12.5))
                .foregroundStyle(DS.Ink.p3)
        }
        .padding(.bottom, 14)
        .overlay(alignment: .bottom) { EditorialRule() }
        .padding(.bottom, 22)
    }

    private func section<Content: View>(label: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(label.uppercased())
                .font(DS.sans(10, weight: .medium))
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
                .font(DS.sans(12.5))
                .foregroundStyle(DS.Ink.p3)
            Spacer()
            Text(value)
                .font(DS.mono(12, weight: .medium))
                .foregroundStyle(valueColor)
        }
        .padding(.vertical, 5)
    }

    // MARK: - Derived values

    private var scoutDirPath: String {
        "~/Scout"
    }

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
