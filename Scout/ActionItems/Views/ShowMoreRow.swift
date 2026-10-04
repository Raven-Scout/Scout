import SwiftUI

/// Footer under a windowed task list: how many rows aren't built yet, and a
/// button that builds the next page. Renders nothing once every row is shown.
struct ShowMoreRow: View {
    let window: TaskWindow
    let tasks: [ActionTask]
    let action: () -> Void

    var body: some View {
        let hidden = window.hiddenCount(in: tasks)
        if hidden > 0 {
            HStack(spacing: 10) {
                Button(action: action) {
                    Label("Show \(window.nextPageCount(in: tasks)) more", systemImage: "chevron.down")
                        .font(DS.sans(11.5, weight: .medium))
                }
                .buttonStyle(.borderless)
                Text("\(hidden) not shown · ⌘F searches all of them")
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Ink.p4)
                Spacer(minLength: 0)
            }
            .padding(.vertical, 6)
            .padding(.horizontal, 4)
        }
    }
}
