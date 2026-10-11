import SwiftUI

/// Header-side twin of ``ShowMoreRow``: "Showing 20 of 53 · Show 20 more",
/// directly under a windowed list's header, so a reader sees that rows are
/// hidden before scrolling to the footer (#356). Renders nothing once every row
/// is shown. It pages like the footer rather than building everything: a
/// "show all" on a backlog of hundreds is the main-thread freeze `TaskWindow`
/// exists to prevent.
struct WindowSummaryRow: View {
    let window: TaskWindow
    let tasks: [ActionTask]
    let action: () -> Void

    var body: some View {
        if let summary = window.summary(in: tasks) {
            HStack(spacing: 8) {
                Text(summary)
                    .font(DS.mono(11, weight: .medium))
                    .foregroundStyle(DS.Ink.p4)
                Text("·")
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Ink.p4)
                    .accessibilityHidden(true)
                Button(action: action) {
                    Text("Show \(window.nextPageCount(in: tasks)) more")
                        .font(DS.sans(11.5, weight: .medium))
                }
                .buttonStyle(.borderless)
                Spacer(minLength: 0)
            }
            // One VoiceOver stop: "Showing 20 of 53, Show 20 more, button".
            .accessibilityElement(children: .combine)
            .padding(.vertical, 4)
            .padding(.horizontal, 4)
        }
    }
}
