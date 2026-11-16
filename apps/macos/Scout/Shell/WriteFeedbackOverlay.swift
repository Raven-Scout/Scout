import AppKit
import SwiftUI

/// The main window's feedback for vault writes (C10): an "Undo" toast after
/// an undoable write, and a problem card when an undo or redo was refused.
/// Sits over the detail pane's bottom-right corner so every page gets the
/// same pattern.
struct WriteFeedbackOverlay: View {
    @ObservedObject var controller: VaultUndoController
    @Environment(\.undoManager) private var undoManager
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// How long the Undo toast stays. Edit ▸ Undo keeps working after.
    static let toastSeconds = 8

    var body: some View {
        VStack(alignment: .trailing, spacing: 8) {
            if let problem = controller.problem {
                WriteProblemBanner(problem: problem) { controller.problem = nil }
                    .transition(.opacity)
            }
            if let latest = controller.latest {
                undoToast(latest)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .task(id: latest) {
                        try? await Task.sleep(for: .seconds(Self.toastSeconds))
                        if controller.latest == latest { controller.dismissLatest() }
                    }
            }
        }
        .padding(.trailing, 20)
        .padding(.bottom, 16)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.18), value: controller.latest)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.18), value: controller.problem)
    }

    private func undoToast(_ latest: VaultUndoController.Registered) -> some View {
        HStack(spacing: 10) {
            Text(Self.doneLine(for: latest.actionName))
                .font(DS.sans(12))
                .foregroundStyle(DS.Ink.p1)
            Button("Undo") { controller.undoLatest(with: undoManager) }
                .buttonStyle(.plainHit)
                .font(DS.sans(12, weight: .semibold))
                .foregroundStyle(DS.Accent.ink)
                .help("Undo (⌘Z)")
            Button { controller.dismissLatest() } label: {
                Image(systemName: "xmark").font(.system(size: 10, weight: .semibold))
            }
            .buttonStyle(.plainHit)
            .foregroundStyle(DS.Ink.p3)
            .accessibilityLabel("Dismiss")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
        .background(Capsule().fill(.regularMaterial))
        .overlay(Capsule().strokeBorder(DS.Rule.soft))
        .shadow(radius: 4, y: 1)
        .accessibilityElement(children: .contain)
    }

    /// "Marked done", "Approved", … — what the toast says happened.
    static func doneLine(for actionName: String) -> String {
        switch actionName {
        case "Mark Done":       return "Marked done"
        case "Reopen":          return "Reopened"
        case "Snooze":          return "Snoozed"
        case "Add Comment":     return "Comment added"
        case "Delete Comment":  return "Comment deleted"
        case "Edit Comment":    return "Comment edited"
        case "Dismiss Entry":   return "Entry dismissed"
        case "Rename Topic":    return "Topic renamed"
        case "Approve":         return "Proposal approved"
        case "Decline":         return "Proposal declined"
        case "Drop":            return "Dropped"
        case "Start":           return "Started"
        case "Change Priority": return "Priority changed"
        case "Change Status":   return "Status changed"
        case "Edit Note":       return "Note saved"
        case "Delete Note":     return "Note deleted"
        case "Rename Note":     return "Note renamed"
        case "Add Slot":        return "Schedule slot added"
        case "Edit Slot":       return "Schedule slot saved"
        case "Delete Slot":     return "Schedule slot deleted"
        default:                return actionName
        }
    }
}

/// One write problem: what happened, what to do, and the raw details behind
/// "Copy details". Interim rendering until C4's shared problem component
/// lands; `WriteProblem` carries the same fields.
struct WriteProblemBanner: View {
    let problem: WriteProblem
    var onRetry: (() -> Void)? = nil
    let onDismiss: () -> Void

    init(problem: WriteProblem, onRetry: (() -> Void)? = nil, onDismiss: @escaping () -> Void) {
        self.problem = problem
        self.onRetry = onRetry
        self.onDismiss = onDismiss
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: problem.kind == .committedOnDiskOnly
                  ? "exclamationmark.triangle.fill" : "xmark.octagon.fill")
                .foregroundStyle(problem.kind == .committedOnDiskOnly ? DS.Status.warn : DS.Status.err)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(problem.title)
                    .font(DS.sans(13, weight: .semibold))
                    .foregroundStyle(DS.Ink.p1)
                Text(problem.message)
                    .font(DS.sans(12))
                    .foregroundStyle(DS.Ink.p2)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 12) {
                    if let onRetry, problem.retryable {
                        Button("Try again", action: onRetry)
                            .buttonStyle(.plainHit)
                            .font(DS.sans(12, weight: .semibold))
                            .foregroundStyle(DS.Accent.ink)
                    }
                    if let details = problem.details {
                        Button("Copy details") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString("\(problem.title)\n\(problem.message)\n\n\(details)", forType: .string)
                        }
                        .buttonStyle(.plainHit)
                        .font(DS.sans(12))
                        .foregroundStyle(DS.Ink.p3)
                    }
                }
                .padding(.top, 2)
            }
            Spacer(minLength: 0)
            Button(action: onDismiss) {
                Image(systemName: "xmark").font(.system(size: 10, weight: .semibold))
            }
            .buttonStyle(.plainHit)
            .foregroundStyle(DS.Ink.p3)
            .accessibilityLabel("Dismiss")
        }
        .padding(12)
        .frame(maxWidth: 420, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(.regularMaterial))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(DS.Rule.soft))
        .shadow(radius: 4, y: 1)
        .accessibilityElement(children: .contain)
    }
}
