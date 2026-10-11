import SwiftUI

/// What an expanded step shows: a body too long for the row, its links, the session when it
/// differs from the topic's, and Launch Claude.
struct StepDetailView: View {
    /// A body this short already reads in full on the row's second line.
    static let rowLineLength = 80
    /// Lines the detail up with the row's title: leading padding, chevron, gap.
    static let titleInset: CGFloat = 7 + 12 + 4

    let task: ActionTask
    let rootSession: String?
    let scoutDirectory: URL

    @State private var launchError: String?

    private var session: String? {
        task.fields.session.flatMap { $0 == rootSession ? nil : $0 }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if task.body.count > Self.rowLineLength {
                InlineMarkdownText(task.body)
                    .font(DS.sans(13))
                    .foregroundStyle(DS.Ink.p2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            TaskLinksView(links: task.deepLinks)
            HStack(spacing: 12) {
                if let session {
                    SessionLink(session: session)
                }
                if let carried = task.carriedInFrom {
                    Text("Carried in \(carried.formatted(.dateTime.day().month(.abbreviated)))")
                }
                Spacer(minLength: 0)
                LaunchClaudeMenu(task: task, scoutDirectory: scoutDirectory, style: .split, launchError: $launchError)
            }
            .font(DS.sans(11.5))
            .foregroundStyle(DS.Ink.p3)
            if let launchError {
                Text(launchError)
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Status.err)
            }
        }
        .padding(.leading, Self.titleInset)
        .padding(.trailing, 14)
        .padding(.bottom, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
