import SwiftUI

// Extracted from SettingsView.swift so sections can live in their own files.
// `internal` rather than `private` for exactly that reason — BudgetSettingsSection
// composes the same atoms. Behavior is unchanged from the original.

// MARK: - Accessibility

extension EnvironmentValues {
    /// The visible title of the settings row or field a control sits in.
    /// `SettingsRow` and `SettingsField` set it, and `SettingsToggle` /
    /// `SettingsInput` use it as their VoiceOver label, so a control is
    /// announced by the words next to it ("Launch at login, switch, on")
    /// without every call site repeating them.
    @Entry var settingsControlLabel: String? = nil
}

/// Set by a control that took the row's title as its VoiceOver label, so the
/// row can hide its own title text and VoiceOver doesn't read it twice. Rows
/// whose trailing item is plain text or a button keep the title readable.
struct SettingsRowTitleUsedKey: PreferenceKey {
    static let defaultValue = false
    static func reduce(value: inout Bool, nextValue: () -> Bool) { value = value || nextValue() }
}

private struct SettingsRowAccessibilityLabel: ViewModifier {
    @Environment(\.settingsControlLabel) private var label

    func body(content: Content) -> some View {
        if let label {
            content.accessibilityLabel(label)
                .preference(key: SettingsRowTitleUsedKey.self, value: true)
        } else {
            content
        }
    }
}

extension View {
    /// Labels a stock control (a `Picker("")` with hidden labels) with the
    /// title of the settings row it sits in, for VoiceOver.
    func labelledBySettingsRow() -> some View {
        modifier(SettingsRowAccessibilityLabel())
    }
}

// MARK: - Building blocks

/// A design-system card (`editorialCard`) holding a stack of settings rows
/// separated by hairlines.
struct SettingsCard<Content: View>: View {
    var padding: CGFloat = 0
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            content()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, padding > 0 ? padding : 0)
        .editorialCard(padding: 0, cornerRadius: 10)
    }
}

/// One row inside a SettingsCard: title + help on the left, trailing control on the right.
struct SettingsRow<Trailing: View>: View {
    let title: String
    let help: String
    @ViewBuilder var trailing: () -> Trailing
    /// True once the trailing control has taken `title` as its label.
    @State private var titleUsedByControl = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 16) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(DS.font(.body, weight: .medium))
                        .foregroundStyle(DS.Ink.p1)
                        .accessibilityHidden(titleUsedByControl)
                    // Help can be the engine's own words (a run-health finding), with commands in backticks.
                    Text(SettingsHelp.attributed(help))
                        .font(DS.font(.caption))
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                trailing()
                    .environment(\.settingsControlLabel, title)
                    .onPreferenceChange(SettingsRowTitleUsedKey.self) { titleUsedByControl = $0 }
            }
            .padding(.vertical, 14)
            Rectangle().fill(DS.Rule.soft).frame(height: 0.5)
                .opacity(0.6)
        }
    }
}

/// Labelled field with help text below the input.
struct SettingsField<Input: View>: View {
    let label: String
    let help: String
    @ViewBuilder var input: () -> Input

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(label.uppercased())
                .font(DS.font(.caption, weight: .medium))
                .tracking(0.06 * DS.scaled(DS.TextStyle.caption.pointSize))
                .foregroundStyle(DS.Ink.p4)
                .accessibilityHidden(true)   // the input below is labelled with it
            input()
                .environment(\.settingsControlLabel, label)
            Text(SettingsHelp.attributed(help))
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p3)
        }
        .padding(.vertical, 14)
    }
}

/// Settings help text: backtick `code` spans in a monospaced caption on a
/// recessed wash, through `PlainText.codeSpans` (no links, nothing else parsed).
enum SettingsHelp {
    static func attributed(_ s: String) -> AttributedString {
        var out = PlainText.codeSpans(s)
        let code = out.runs.filter { $0.inlinePresentationIntent?.contains(.code) == true }.map(\.range)
        for range in code {
            out[range].font = DS.font(.caption, .mono)
            out[range].backgroundColor = DS.Paper.sunk
        }
        return out
    }
}

/// Pill-shaped input on a recessed paper field.
struct SettingsInput: View {
    @Binding var text: String
    let placeholder: String
    /// VoiceOver label; defaults to the enclosing row's or field's title.
    var accessibilityLabel: String? = nil
    @Environment(\.settingsControlLabel) private var rowLabel

    var body: some View {
        TextField(placeholder, text: $text)
            .accessibilityLabel(accessibilityLabel ?? rowLabel ?? placeholder)
            .preference(key: SettingsRowTitleUsedKey.self, value: accessibilityLabel == nil && rowLabel != nil)
            .dsField()
    }
}

/// Pill toggle that visually echoes the design's `.set-switch.on` accent fill.
/// A button underneath, so it carries the toggle trait, an On/Off value and
/// the row's title for VoiceOver (it used to be an unnamed button).
struct SettingsToggle: View {
    @Binding var isOn: Bool
    /// VoiceOver label; defaults to the enclosing row's title.
    var accessibilityLabel: String? = nil
    @Environment(\.settingsControlLabel) private var rowLabel

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
            .motionAnimation(.easeInOut(duration: 0.15), value: isOn)
        }
        .buttonStyle(.plainHit)
        .accessibilityLabel(ifPresent: accessibilityLabel ?? rowLabel)
        .preference(key: SettingsRowTitleUsedKey.self, value: accessibilityLabel == nil && rowLabel != nil)
        .accessibilityValue(isOn ? "On" : "Off")
        .accessibilityAddTraits(.isToggle)
    }
}
