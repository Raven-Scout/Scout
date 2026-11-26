import SwiftUI
import AppKit

/// The run detail's Errors tab: what `RunLogErrorScanner` found in the log,
/// each with a plain label ("Python error", not the pattern that matched) and,
/// for a traceback or other multi-line error, the whole block on request.
struct ErrorsTab: View {
    let errors: [DetectedError]
    /// The run failed, so an empty list must not read as "nothing went wrong".
    var runFailed: Bool = false

    var body: some View {
        if errors.isEmpty {
            Text(runFailed
                 ? "This run failed, but its log has no error line Scout recognises. The Log tab shows the whole log."
                 : "No errors in this run's log.")
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center)
                .padding()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(errors.enumerated()), id: \.offset) { _, error in
                        ErrorRow(error: error)
                            .overlay(alignment: .bottom) { Divider() }
                    }
                }
            }
        }
    }
}

private struct ErrorRow: View {
    let error: DetectedError
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(error.kind.displayName) · line \(error.line)")
                .font(DS.font(.caption2, weight: .medium))
                .foregroundStyle(error.kind == .runner ? DS.Status.warn : DS.Status.err)
            Text(error.snippet)
                .font(DS.font(.label, .mono))
                .foregroundStyle(DS.Ink.p1)
                .lineLimit(2)
                .textSelection(.enabled)
            if !error.block.isEmpty {
                HStack(spacing: 12) {
                    Button(expanded ? "Hide the \(error.block.count) lines" : "Show all \(error.block.count) lines") {
                        expanded.toggle()
                    }
                    .buttonStyle(.ds(.link, size: .small))
                    .accessibilityHint("Shows every line of this error")
                    Button("Copy") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(error.block.joined(separator: "\n"), forType: .string)
                    }
                    .buttonStyle(.ds(.link, size: .small))
                    .accessibilityLabel("Copy this error's \(error.block.count) lines")
                }
                if expanded {
                    ScrollView(.horizontal) {
                        Text(error.block.joined(separator: "\n"))
                            .font(DS.font(.caption, .mono))
                            .foregroundStyle(DS.Ink.p2)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: true, vertical: true)
                            .padding(8)
                    }
                    .background(DS.Paper.raised, in: RoundedRectangle(cornerRadius: 6))
                }
            }
        }
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(error.kind.displayName) on line \(error.line): \(error.snippet)")
    }
}
