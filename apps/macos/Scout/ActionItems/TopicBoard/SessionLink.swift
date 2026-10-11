import SwiftUI

/// A Claude session reference: opens a `claude.ai` link, otherwise shows the
/// local session id as text.
struct SessionLink: View {
    let session: String

    private var url: URL? {
        guard case .cloud(let url) = SessionReference(session) else { return nil }
        return url
    }

    var body: some View {
        if let url {
            Button {
                NSWorkspace.shared.open(url)
            } label: {
                Label("Session", systemImage: "sparkles")
            }
            .buttonStyle(.plainHit)
            .help(session)
        } else {
            Label("Session \(session)", systemImage: "sparkles")
                .lineLimit(1)
                .textSelection(.enabled)
        }
    }
}
