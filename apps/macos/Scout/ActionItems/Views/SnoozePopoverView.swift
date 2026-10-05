import SwiftUI

/// Snooze menu with quick presets and an "Other date…" expansion for the
/// less-common case. Earlier revisions tried to ship a ``DatePicker`` seeded
/// via ``State(initialValue:)`` in ``init``; on macOS 26 inside ``.popover``
/// the wrapper would sometimes silently drop the seed and ``picked`` would
/// read as ``Date(timeIntervalSinceReferenceDate: 0)`` — formatted in ET
/// that's ``2000-12-31``, which is what ``snooze.py`` saw on the command
/// line. The custom date path here re-seeds the binding from ``sourceDate``
/// inside ``onAppear`` of the expanded panel so the picker never relies on
/// an `init`-time State value.
struct SnoozePopoverView: View {
    let sourceDate: Date
    let onCommit: (Date) async -> Void
    let onCancel: () -> Void

    @State private var submitting = false
    @State private var showingCustom = false
    /// Sentinel for "uninitialized" — the picker never reads this value
    /// directly; ``customPanel.onAppear`` overwrites it before the view
    /// renders. Using `.distantPast` guarantees we'd notice a regression
    /// to the macOS-26 epoch-leak bug immediately rather than silently
    /// committing it as a real date.
    @State private var customDate: Date = .distantPast

    private static let presets: [(label: String, days: Int)] = [
        ("Tomorrow",     1),
        ("In 3 days",    3),
        ("Next week",    7),
        ("In 2 weeks",   14),
        ("Next month",   30),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if showingCustom {
                customPanel
            } else {
                presetPanel
            }
        }
        .frame(width: 240)
        .padding(.vertical, 4)
    }

    // MARK: - Preset panel

    private var presetPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            header(title: "Snooze until…")
            Divider().padding(.horizontal, 8)
            ForEach(Self.presets, id: \.label) { preset in
                presetRow(label: preset.label, days: preset.days)
            }
            Button {
                showingCustom = true
            } label: {
                HStack {
                    Text("Other date…").font(.system(size: 12))
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plainHit)
            .disabled(submitting)

            Divider().padding(.horizontal, 8)
            Button("Cancel", action: onCancel)
                .buttonStyle(.plainHit)
                .foregroundStyle(.secondary)
                .font(.caption)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
                .keyboardShortcut(.cancelAction)
        }
    }

    // MARK: - Custom date panel

    private var customPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                Button {
                    showingCustom = false
                } label: {
                    Image(systemName: "chevron.left").font(.system(size: 10))
                }
                .buttonStyle(.plainHit)
                .foregroundStyle(.secondary)
                Text("Pick a date")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)

            Divider().padding(.horizontal, 8)

            DatePicker(
                "Snooze until",
                selection: $customDate,
                in: minimumCustomDate...,
                displayedComponents: [.date]
            )
            .datePickerStyle(.graphical)
            .labelsHidden()
            .padding(.horizontal, 10)
            .padding(.vertical, 6)

            HStack(spacing: 8) {
                Spacer()
                Button("Cancel", action: onCancel)
                    .buttonStyle(.plainHit)
                    .foregroundStyle(.secondary)
                // Matches the unstyled "Send" button in
                // CommentComposerView — Alex's UI pass (eb88094) targeted
                // toggles/segmented controls, leaving primary-action buttons
                // on the system style. Stay consistent with that.
                Button("Snooze") { commitCustom() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(submitting || customDate <= sourceDate)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .frame(width: 280)
        .onAppear {
            // Lazy seed — see the file-level note about the macOS-26 popover
            // bug. Default to tomorrow as a sensible starting point.
            if customDate == .distantPast {
                customDate = Calendar(identifier: .iso8601)
                    .date(byAdding: .day, value: 1, to: sourceDate) ?? sourceDate
            }
        }
    }

    // MARK: - Common pieces

    private func header(title: String) -> some View {
        Text(title)
            .font(.caption.weight(.semibold))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
            .padding(.top, 6)
            .padding(.bottom, 6)
    }

    private func presetRow(label: String, days: Int) -> some View {
        Button {
            commit(days: days)
        } label: {
            HStack {
                Text(label).font(.system(size: 12))
                Spacer(minLength: 8)
                Text(relativeLabel(days: days))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plainHit)
        .disabled(submitting)
    }

    private func commit(days: Int) {
        guard let target = Calendar(identifier: .iso8601)
            .date(byAdding: .day, value: days, to: sourceDate),
              target > sourceDate
        else { return }
        submitting = true
        Task { await onCommit(target) }
    }

    private func commitCustom() {
        guard customDate > sourceDate, customDate != .distantPast else { return }
        submitting = true
        let target = customDate
        Task { await onCommit(target) }
    }

    private var minimumCustomDate: Date {
        Calendar(identifier: .iso8601)
            .date(byAdding: .day, value: 1, to: sourceDate) ?? sourceDate
    }

    private func relativeLabel(days: Int) -> String {
        guard let d = Calendar(identifier: .iso8601)
            .date(byAdding: .day, value: days, to: sourceDate) else { return "" }
        let fmt = DateFormatter()
        fmt.dateFormat = "EEE MMM d"
        fmt.timeZone = .current
        return fmt.string(from: d)
    }
}
