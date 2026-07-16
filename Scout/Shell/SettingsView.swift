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
                            title: "Launch minimized",
                            help: "Start with only the menu-bar panel visible. Open the full window whenever you need it."
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
        // Release builds get a stamped MARKETING_VERSION via scripts/release.sh;
        // dev builds keep the xcodeproj default (1.0), which reads like a real
        // release. Mark them as dev and show the build time so it's obvious
        // which local build is running.
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

// MARK: - Building blocks

/// Recessed paper card holding a stack of settings rows separated by hairlines.
private struct SettingsCard<Content: View>: View {
    var padding: CGFloat = 0
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            content()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, padding > 0 ? padding : 0)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
    }
}

/// One row inside a SettingsCard: title + help on the left, trailing control on the right.
private struct SettingsRow<Trailing: View>: View {
    let title: String
    let help: String
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 16) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(DS.sans(13, weight: .medium))
                        .foregroundStyle(DS.Ink.p1)
                    Text(help)
                        .font(DS.sans(11.5))
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                trailing()
            }
            .padding(.vertical, 14)
            Rectangle().fill(DS.Rule.soft).frame(height: 0.5)
                .opacity(0.6)
        }
    }
}

/// Labelled field with help text below the input.
private struct SettingsField<Input: View>: View {
    let label: String
    let help: String
    @ViewBuilder var input: () -> Input

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(label.uppercased())
                .font(DS.sans(11, weight: .medium))
                .tracking(0.06 * 11)
                .foregroundStyle(DS.Ink.p4)
            input()
            Text(parseHelp(help))
                .font(DS.sans(11.5))
                .foregroundStyle(DS.Ink.p3)
        }
        .padding(.vertical, 14)
    }

    /// Light renderer for backtick-wrapped `code` spans in help text.
    private func parseHelp(_ s: String) -> AttributedString {
        var out = AttributedString()
        var rest = s[...]
        while let openIdx = rest.firstIndex(of: "`"),
              let closeIdx = rest[rest.index(after: openIdx)...].firstIndex(of: "`") {
            out.append(AttributedString(rest[rest.startIndex..<openIdx]))
            var code = AttributedString(rest[rest.index(after: openIdx)..<closeIdx])
            code.font = DS.mono(11)
            code.backgroundColor = DS.Paper.sunk
            out.append(code)
            rest = rest[rest.index(after: closeIdx)...]
        }
        out.append(AttributedString(rest))
        return out
    }
}

/// Pill-shaped input on a recessed paper field.
private struct SettingsInput: View {
    @Binding var text: String
    let placeholder: String

    var body: some View {
        TextField(placeholder, text: $text)
            .textFieldStyle(.plain)
            .font(DS.sans(13, weight: .medium))
            .foregroundStyle(DS.Ink.p1)
            .padding(.horizontal, 10)
            .frame(height: 30)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(DS.Paper.sunk)
                    .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
            )
    }
}

/// Pill toggle that visually echoes the design's `.set-switch.on` accent fill.
private struct SettingsToggle: View {
    @Binding var isOn: Bool

    var body: some View {
        Button { isOn.toggle() } label: {
            ZStack(alignment: isOn ? .trailing : .leading) {
                Capsule()
                    .fill(isOn ? DS.Accent.fill : DS.Paper.sunk)
                    .overlay(Capsule().strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                Circle()
                    .fill(.white)
                    .shadow(color: DS.Neumorphic.shadow.opacity(0.5), radius: 1, y: 1)
                    .frame(width: 16, height: 16)
                    .padding(2)
            }
            .frame(width: 36, height: 20)
            .animation(.easeInOut(duration: 0.15), value: isOn)
        }
        .buttonStyle(.plainHit)
    }
}
