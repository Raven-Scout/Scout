import AppKit
import SwiftUI

/// A button `ProblemView` shows under a problem: Retry, Open Settings,
/// Update. The first one is drawn as the primary action.
struct ProblemAction: Identifiable {
    let id: String
    let title: String
    let perform: @MainActor () -> Void

    init(_ title: String, perform: @escaping @MainActor () -> Void) {
        self.id = title
        self.title = title
        self.perform = perform
    }

    static func retry(_ perform: @escaping @MainActor () -> Void) -> ProblemAction {
        ProblemAction("Try again", perform: perform)
    }
}

/// The one way the app shows a problem: what happened (title), why it
/// matters (impact), what to do (fix), then buttons, and the engine's own
/// words behind "Details" with "Copy details".
///
/// `.banner` draws through `DSBanner`, the design system's banner; `.inline`
/// drops the background for use inside a card or a settings row.
struct ProblemView: View {
    enum Style { case banner, inline }

    let problem: Problem
    var actions: [ProblemAction] = []
    var style: Style = .banner
    /// Shown as an × at the trailing edge when set.
    var onDismiss: (() -> Void)? = nil

    @State private var showDetails = false
    @State private var copied = false
    /// "Resolve with Claude" couldn't build its prompt or launch.
    @State private var launchError: String?
    @Environment(\.resolvePromptService) private var resolveService

    /// Whether the problem gets "Resolve with Claude": it names something to investigate and
    /// the app gave this view the engine's prompt service. (The button itself also waits for
    /// the engine to advertise `resolve_prompt_v1`.)
    static func showsResolve(problem: Problem, service: ResolvePromptService?) -> Bool {
        problem.resolveSubject != nil && service != nil
    }

    var body: some View {
        switch style {
        case .banner:
            DSBanner(tone: Self.tone(problem.severity), accessibilityTitle: problem.title, onDismiss: onDismiss) {
                lines
            }
        case .inline:
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: Self.symbol(problem.severity))
                    .foregroundStyle(Self.tint(problem.severity))
                    .font(DS.font(.body, weight: .semibold))
                    .accessibilityHidden(true)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 4) { lines }
                Spacer(minLength: 0)
                if let onDismiss {
                    Button(action: onDismiss) {
                        Image(systemName: "xmark").font(DS.font(.caption2, weight: .semibold))
                    }
                    .buttonStyle(.ds(.quiet, size: .small))
                    .help("Dismiss")
                    .accessibilityLabel("Dismiss")
                }
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel("\(Self.severityWord(problem.severity)): \(problem.title)")
        }
    }

    /// What happened, why it matters, what to do, the buttons and the details.
    @ViewBuilder private var lines: some View {
        Text(problem.title)
            .font(DS.font(.label, weight: .semibold))
            .foregroundStyle(DS.Ink.p1)
            .fixedSize(horizontal: false, vertical: true)
        if let impact = problem.impact {
            Text(impact)
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
        }
        if let fix = problem.fix {
            (Text("What to do: ").fontWeight(.semibold) + Text(fix))
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p1)
                .fixedSize(horizontal: false, vertical: true)
        }
        buttons
        if showDetails, let details = problem.details {
            Text(details)
                .font(DS.font(.caption, .mono))
                .foregroundStyle(DS.Ink.p2)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk))
        }
    }

    @ViewBuilder private var buttons: some View {
        let resolves = Self.showsResolve(problem: problem, service: resolveService)
        if !actions.isEmpty || problem.details != nil || resolves {
            DSBannerActions(actions: actions) {
                if resolves, let subject = problem.resolveSubject, let resolveService {
                    ResolveWithClaudeButton(subject: subject, service: resolveService, launchError: $launchError)
                }
                if problem.details != nil {
                    Button(showDetails ? "Hide details" : "Details") { showDetails.toggle() }
                        .buttonStyle(.ds(.link, size: .small))
                    Button(copied ? "Copied" : "Copy details") {
                        Self.copy(problem.copyText)
                        copied = true
                        Task {
                            try? await Task.sleep(for: .seconds(2))
                            copied = false
                        }
                    }
                    .buttonStyle(.ds(.link, size: .small))
                }
            }
        }
        if let launchError {
            Text(launchError)
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    static func tone(_ s: Problem.Severity) -> DS.BannerTone {
        switch s {
        case .info:    return .info
        case .warning: return .warning
        case .error:   return .error
        }
    }

    static func symbol(_ s: Problem.Severity) -> String { tone(s).symbol }

    static func tint(_ s: Problem.Severity) -> Color { tone(s).tint.color }

    static func severityWord(_ s: Problem.Severity) -> String { tone(s).word }
}

#if DEBUG
#Preview("Problem banner") {
    VStack(spacing: 12) {
        ProblemView(problem: Problem(failureClass: "oauth_expired", details: "Log: /tmp/run.log\nExit code: 1"),
                    actions: [.retry {}])
        ProblemView(problem: Problem(runStatus: "skippedBudget", runName: "Morning briefing")!)
        ProblemView(problem: .engineUnavailable("Sessions", details: "scoutctl: command not found"),
                    style: .inline)
    }
    .padding()
    .frame(width: 520)
}
#endif
