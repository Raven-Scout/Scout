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
/// `.banner` is a full-width strip for the top of a page; `.inline` drops the
/// background for use inside a card or a settings row.
struct ProblemView: View {
    enum Style { case banner, inline }

    let problem: Problem
    var actions: [ProblemAction] = []
    var style: Style = .banner
    /// Shown as an × at the trailing edge when set.
    var onDismiss: (() -> Void)? = nil

    @State private var showDetails = false
    @State private var copied = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: Self.symbol(problem.severity))
                .foregroundStyle(Self.tint(problem.severity))
                .font(.system(size: 13, weight: .semibold))
                .accessibilityHidden(true)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 4) {
                Text(problem.title)
                    .font(DS.sans(12.5, weight: .semibold))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                if let impact = problem.impact {
                    Text(impact)
                        .font(DS.sans(12))
                        .foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let fix = problem.fix {
                    (Text("What to do: ").fontWeight(.semibold) + Text(fix))
                        .font(DS.sans(12))
                        .foregroundStyle(DS.Ink.p1)
                        .fixedSize(horizontal: false, vertical: true)
                }
                buttons
                if showDetails, let details = problem.details {
                    Text(details)
                        .font(DS.mono(11))
                        .foregroundStyle(DS.Ink.p2)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk))
                }
            }
            Spacer(minLength: 0)
            if let onDismiss {
                Button(action: onDismiss) {
                    Image(systemName: "xmark").font(.system(size: 10, weight: .semibold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(DS.Ink.p3)
                .help("Dismiss")
                .accessibilityLabel("Dismiss")
            }
        }
        .padding(style == .banner ? 12 : 0)
        .background {
            if style == .banner {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Self.tint(problem.severity).opacity(0.08))
                    .overlay(RoundedRectangle(cornerRadius: 8)
                        .strokeBorder(Self.tint(problem.severity).opacity(0.35), lineWidth: 1))
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(Self.severityWord(problem.severity)): \(problem.title)")
    }

    @ViewBuilder private var buttons: some View {
        if !actions.isEmpty || problem.details != nil {
            HStack(spacing: 8) {
                ForEach(Array(actions.enumerated()), id: \.element.id) { i, action in
                    if i == 0 {
                        Button(action.title) { action.perform() }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.small)
                    } else {
                        Button(action.title) { action.perform() }
                            .buttonStyle(.bordered)
                            .controlSize(.small)
                    }
                }
                if problem.details != nil {
                    Button(showDetails ? "Hide details" : "Details") { showDetails.toggle() }
                        .buttonStyle(.link)
                        .controlSize(.small)
                    Button(copied ? "Copied" : "Copy details") {
                        Self.copy(problem.copyText)
                        copied = true
                        Task {
                            try? await Task.sleep(for: .seconds(2))
                            copied = false
                        }
                    }
                    .buttonStyle(.link)
                    .controlSize(.small)
                }
            }
            .padding(.top, 2)
        }
    }

    static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    static func symbol(_ s: Problem.Severity) -> String {
        switch s {
        case .info:    return "info.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .error:   return "xmark.octagon.fill"
        }
    }

    static func tint(_ s: Problem.Severity) -> Color {
        switch s {
        case .info:    return DS.Ink.p3
        case .warning: return DS.Status.warn
        case .error:   return DS.Status.err
        }
    }

    static func severityWord(_ s: Problem.Severity) -> String {
        switch s {
        case .info:    return "Note"
        case .warning: return "Warning"
        case .error:   return "Problem"
        }
    }
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
