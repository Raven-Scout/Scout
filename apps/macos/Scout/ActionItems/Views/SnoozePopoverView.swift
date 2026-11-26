import SwiftUI

/// Snooze menu with quick presets and an "Other date…" expansion for the
/// less-common case. Earlier revisions tried to ship a ``DatePicker`` seeded
/// via ``State(initialValue:)`` in ``init``; on macOS 26 inside ``.popover``
/// the wrapper would sometimes silently drop the seed and ``picked`` would
/// read as ``Date(timeIntervalSinceReferenceDate: 0)`` — formatted in ET
/// that's ``2000-12-31``, which is what ``snooze.py`` saw on the command
/// line. The custom date path here re-seeds the binding (to the day after
/// ``SnoozeDates/anchor(sourceDate:now:calendar:)``) inside ``onAppear`` of the expanded panel so the picker never relies on
/// an `init`-time State value.
///
/// Presets count from the real today, not the displayed day (audit #46): on
/// an old file, "Tomorrow" counted from that file's day was already in the
/// past. See `SnoozeDates`.
struct SnoozePopoverView: View {
    /// The day of the file the task is on.
    let sourceDate: Date
    let onCommit: (Date) async -> Void
    let onCancel: () -> Void
    /// Injected for tests; the clock otherwise. Last, so the trailing
    /// closures at the call sites still match `onCommit` and `onCancel`.
    var now: () -> Date = { .now }

    @State private var submitting = false
    @State private var showingCustom = false
    /// Sentinel for "uninitialized" — the picker never reads this value
    /// directly; ``customPanel.onAppear`` overwrites it before the view
    /// renders. Using `.distantPast` guarantees we'd notice a regression
    /// to the macOS-26 epoch-leak bug immediately rather than silently
    /// committing it as a real date.
    @State private var customDate: Date = .distantPast

    /// Also the Snooze submenu of a card's context menu.
    static let presets: [(label: String, days: Int)] = [
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
                    Text("Other date…").font(DS.font(.label))
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right")
                        .font(DS.font(.micro))
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
                .buttonStyle(.ds(.quiet, size: .small))
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
                    Image(systemName: "chevron.left").font(DS.font(.caption2))
                }
                .buttonStyle(.plainHit)
                .accessibilityLabel("Back")
                .foregroundStyle(.secondary)
                Text("Pick a date")
                    .font(DS.font(.caption2, weight: .semibold))
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
                    .buttonStyle(.dsQuiet)
                Button("Snooze") { commitCustom() }
                    .buttonStyle(.dsPrimary)
                    .keyboardShortcut(.defaultAction)
                    .disabled(submitting || customDate < minimumCustomDate)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .frame(width: 280)
        .onAppear {
            // Lazy seed — see the file-level note about the macOS-26 popover
            // bug. Default to tomorrow as a sensible starting point.
            if customDate == .distantPast {
                customDate = minimumCustomDate
            }
        }
    }

    // MARK: - Common pieces

    private func header(title: String) -> some View {
        Text(title)
            .font(DS.font(.caption2, weight: .semibold))
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
                Text(label).font(DS.font(.label))
                Spacer(minLength: 8)
                Text(relativeLabel(days: days))
                    .font(DS.font(.caption2))
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plainHit)
        .disabled(submitting)
    }

    /// The date a preset snoozes to, for this popover and the card's
    /// right-click Snooze menu alike: counted from the real today, or from
    /// `sourceDate` (the day on screen) when that is later (audit #46, via
    /// `SnoozeDates`).
    static func presetTarget(days: Int, from sourceDate: Date, now: Date = .now) -> Date? {
        SnoozeDates.target(days: days, sourceDate: sourceDate, now: now)
    }

    private func commit(days: Int) {
        guard let target = Self.presetTarget(days: days, from: sourceDate, now: now()) else { return }
        submitting = true
        Task { await onCommit(target) }
    }

    private func commitCustom() {
        guard customDate >= minimumCustomDate, customDate != .distantPast else { return }
        submitting = true
        let target = customDate
        Task { await onCommit(target) }
    }

    private var minimumCustomDate: Date {
        SnoozeDates.target(days: 1, sourceDate: sourceDate, now: now()) ?? sourceDate
    }

    private func relativeLabel(days: Int) -> String {
        guard let d = SnoozeDates.target(days: days, sourceDate: sourceDate, now: now()) else { return "" }
        return d.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())
    }
}

/// The dates the snooze presets pick.
nonisolated enum SnoozeDates {
    /// The day presets count from: the real today, or the displayed day when
    /// that is later. Counting from an old file's day made "Tomorrow" a date
    /// already past (audit #46).
    static func anchor(sourceDate: Date, now: Date, calendar: Calendar = .current) -> Date {
        max(calendar.startOfDay(for: now), calendar.startOfDay(for: sourceDate))
    }

    /// `days` after the anchor, or `nil` when the calendar can't say.
    static func target(days: Int, sourceDate: Date, now: Date, calendar: Calendar = .current) -> Date? {
        calendar.date(byAdding: .day, value: days, to: anchor(sourceDate: sourceDate, now: now, calendar: calendar))
    }
}
