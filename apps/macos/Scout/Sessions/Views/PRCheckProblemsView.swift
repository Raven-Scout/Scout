import AppKit
import SwiftUI

/// What "PR check: 2 problems" opens: one line per pull request Scout
/// couldn't check through `gh`, with Check again and Copy details.
struct PRCheckProblemsView: View {
    let errors: [SessionSourceError]
    let finishedAt: Date?
    let now: Date
    let onCheckAgain: () -> Void

    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(errors.count == 1 ? "PR check problem" : "PR check problems")
                .font(DS.font(.headline, .serif, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
            Text(SessionsFormat.prProblemsSummary(count: errors.count))
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
            ScrollView {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(errors.enumerated()), id: \.offset) { _, error in
                        Label {
                            Text(error.message)
                                .font(DS.font(.caption, .mono))
                                .foregroundStyle(DS.Ink.p2)
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        } icon: {
                            Image(systemName: "exclamationmark.triangle")
                                .foregroundStyle(DS.Status.warn)
                                .accessibilityHidden(true)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 220)
            if let finishedAt {
                Text("Last checked \(SessionsFormat.ago(finishedAt, now: now))")
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p4)
            }
            HStack(spacing: 8) {
                Button("Check again", action: onCheckAgain)
                Button(copied ? "Copied" : "Copy details") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(Self.details(errors), forType: .string)
                    copied = true
                }
            }
            .controlSize(.small)
            .font(DS.font(.label))
        }
        .padding(16)
        .frame(width: 380, alignment: .leading)
    }

    /// One `source: message` line per problem, for a bug report.
    nonisolated static func details(_ errors: [SessionSourceError]) -> String {
        errors.map { "\($0.source): \($0.message)" }.joined(separator: "\n")
    }
}
