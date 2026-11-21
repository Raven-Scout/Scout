import SwiftUI

/// Header-side twin of ``ShowMoreRow``: "Showing 20 of 53 · Show 20 more",
/// directly under a windowed list's header, so a reader sees that rows are
/// hidden before scrolling to the footer (#356). Renders nothing once every row
/// is shown. It pages like the footer, and offers "Show all" only for a small
/// remainder (`TaskWindow.showAllLimit`): building a backlog of hundreds at
/// once is the main-thread freeze `TaskWindow` exists to prevent.
struct WindowSummaryRow: View {
    let window: TaskWindow
    let tasks: [ActionTask]
    let action: () -> Void
    /// Builds every remaining row. Offered only when ``TaskWindow/canShowAll(in:)``.
    var showAll: (() -> Void)? = nil

    var body: some View {
        if let summary = window.summary(in: tasks) {
            HStack(spacing: 8) {
                Text(summary)
                    .font(DS.font(.caption, weight: .medium))
                    .foregroundStyle(DS.Ink.p4)
                Text("·")
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p4)
                    .accessibilityHidden(true)
                Button(action: action) {
                    Text("Show \(window.nextPageCount(in: tasks)) more")
                        .font(DS.font(.caption, weight: .medium))
                }
                .buttonStyle(.borderless)
                .dsReportsOwnFocus()
                if let showAll, window.canShowAll(in: tasks) {
                    Button("Show all", action: showAll)
                        .font(DS.font(.caption, weight: .medium))
                        .buttonStyle(.borderless)
                        .dsReportsOwnFocus()
                }
                Spacer(minLength: 0)
            }
            // VoiceOver: the summary, then each button as its own stop; the
            // "·" is decoration.
            .accessibilityElement(children: .contain)
            .padding(.vertical, 4)
            .padding(.horizontal, 4)
        }
    }
}
