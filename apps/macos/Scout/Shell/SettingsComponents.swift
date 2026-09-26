import SwiftUI

// Extracted from SettingsView.swift so sections can live in their own files.
// `internal` rather than `private` for exactly that reason — BudgetSettingsSection
// composes the same atoms. Behavior is unchanged from the original.

// MARK: - Building blocks

/// Recessed paper card holding a stack of settings rows separated by hairlines.
struct SettingsCard<Content: View>: View {
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
struct SettingsRow<Trailing: View>: View {
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
struct SettingsField<Input: View>: View {
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
struct SettingsInput: View {
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
struct SettingsToggle: View {
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
