import SwiftUI

/// Scrollable monospace rendering of one file's patch with +/− coloring.
struct CommitDiffView: View {
    let patch: String

    private var lines: [Substring] { patch.split(separator: "\n", omittingEmptySubsequences: false) }

    var body: some View {
        ScrollView([.vertical, .horizontal]) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(line.isEmpty ? " " : String(line))
                        .font(DS.mono(11))
                        .foregroundStyle(color(for: line))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .textSelection(.enabled)
            .padding(10)
        }
        .frame(maxHeight: 320)
        .background(DS.Paper.base)
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func color(for line: Substring) -> Color {
        if line.hasPrefix("+") && !line.hasPrefix("+++") { return DS.Status.ok }
        if line.hasPrefix("-") && !line.hasPrefix("---") { return DS.Status.err }
        if line.hasPrefix("@@") { return DS.Ink.p3 }
        return DS.Ink.p2
    }
}
