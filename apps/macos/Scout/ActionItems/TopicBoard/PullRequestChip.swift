import SwiftUI

/// A PR as a short `repo#N` link with a CI dot.
struct PullRequestChip: View {
    let label: String
    let url: URL?
    var ciColor: Color = DS.Priority.done

    var body: some View {
        Button {
            url.map { _ = NSWorkspace.shared.open($0) }
        } label: {
            HStack(spacing: 5) {
                Circle().fill(ciColor).frame(width: 6, height: 6)
                Text(label)
            }
        }
        .buttonStyle(.plainHit)
        .disabled(url == nil)
        .help(url?.absoluteString ?? label)
    }
}
