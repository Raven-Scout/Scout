import SwiftUI

/// Editable form state for the four budget knobs.
///
/// Held as strings because the fields are free-text `SettingsInput`s and a
/// half-typed value ("2", on the way to "200") must not be coerced into a
/// number mid-keystroke. Bounds mirror the engine's `CONFIG_BOUNDS` so an
/// out-of-range value is caught here rather than costing a subprocess and
/// surfacing as raw stderr.
struct BudgetDraft: Equatable {
    var dailyUSD: String
    var windowHours: String
    var skipAtPct: String
    var failureBackoffMinutes: String

    init(from settings: BudgetSettings) {
        self.dailyUSD = Self.display(settings.dailyUSD)
        self.windowHours = "\(settings.windowHours)"
        self.skipAtPct = Self.display(settings.skipAtPct)
        self.failureBackoffMinutes = "\(settings.failureBackoffMinutes)"
    }

    /// The four values, or nil when any field is empty, non-numeric, or out of
    /// the range the engine will accept.
    var parsed: (daily: Double, hours: Int, pct: Double, backoff: Int)? {
        guard let daily = Double(dailyUSD.budgetTrimmed), daily >= 0,
              let hours = Int(windowHours.budgetTrimmed), hours >= 1,
              let pct = Double(skipAtPct.budgetTrimmed), (0...100).contains(pct),
              let backoff = Int(failureBackoffMinutes.budgetTrimmed), backoff >= 0
        else { return nil }
        return (daily, hours, pct, backoff)
    }

    /// A specific reason the draft cannot be saved, or nil when it can.
    var validationMessage: String? {
        guard parsed == nil else { return nil }
        if let daily = Double(dailyUSD.budgetTrimmed) {
            if daily < 0 { return "Daily budget cannot be negative." }
        } else {
            return "Daily budget must be a number."
        }
        if let hours = Int(windowHours.budgetTrimmed) {
            if hours < 1 { return "The rolling window must be at least 1 hour." }
        } else {
            return "The rolling window must be a whole number of hours."
        }
        if let pct = Double(skipAtPct.budgetTrimmed) {
            if !(0...100).contains(pct) { return "The skip threshold must be between 0 and 100 percent." }
        } else {
            return "The skip threshold must be a number."
        }
        if let backoff = Int(failureBackoffMinutes.budgetTrimmed) {
            if backoff < 0 { return "The failure backoff cannot be negative." }
        } else {
            return "The failure backoff must be a whole number of minutes."
        }
        return "Enter a value in every field."
    }

    /// `200`, not `200.0` — these round-trip through YAML a person reads.
    private static func display(_ value: Double) -> String {
        value == value.rounded() ? "\(Int(value))" : "\(value)"
    }
}

private extension String {
    var budgetTrimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}

/// The Budget section of Settings: the four knobs `scoutctl budget check`
/// enforces, plus the gate they compute to.
///
/// Configuration only — no spend figures. `UsageRailCard` deliberately omits
/// dollar cost as misleading on a quota-based plan seat, and this section keeps
/// that line.
struct BudgetSettingsSection: View {
    @EnvironmentObject var service: BudgetSettingsService

    @State private var draft: BudgetDraft?
    @State private var loadError: String?
    @State private var saveError: String?
    @State private var isSaving = false

    var body: some View {
        SettingsCard {
            if let draft {
                fields
                footer(draft)
            } else if let loadError {
                message(loadError, color: DS.Status.warn)
            } else {
                message("Reading budget configuration…", color: DS.Ink.p4)
            }
        }
        .task { await load() }
    }

    // MARK: - Fields

    /// The four inputs. Reads and writes through `binding`, so it needs no
    /// draft parameter — SwiftUI redraws it when `@State draft` changes.
    @ViewBuilder
    private var fields: some View {
        SettingsField(
            label: "Daily budget",
            help: "USD per day the scheduled runs may spend. Prorated to the rolling window below."
        ) {
            SettingsInput(text: binding(\.dailyUSD), placeholder: "50")
        }
        SettingsField(
            label: "Rolling window",
            help: "Hours of spend history the gate sums. A shorter window means a tighter effective cap."
        ) {
            SettingsInput(text: binding(\.windowHours), placeholder: "5")
        }
        SettingsField(
            label: "Skip threshold",
            help: "Percent of the window budget at which a scheduled session is skipped."
        ) {
            SettingsInput(text: binding(\.skipAtPct), placeholder: "80")
        }
        SettingsField(
            label: "Failure backoff",
            help: "Minutes to wait after a failed run. Rate-limit events back off for twice this long."
        ) {
            SettingsInput(text: binding(\.failureBackoffMinutes), placeholder: "60")
        }
    }

    private func binding(_ key: WritableKeyPath<BudgetDraft, String>) -> Binding<String> {
        Binding(
            get: { draft?[keyPath: key] ?? "" },
            set: { newValue in
                guard var updated = draft else { return }
                updated[keyPath: key] = newValue
                draft = updated
                saveError = nil
            }
        )
    }

    // MARK: - Footer: derived gate, advisories, Save

    @ViewBuilder
    private func footer(_ current: BudgetDraft) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            gateLine(current)
            if let message = current.validationMessage {
                advisory(message, color: DS.Status.warn)
            }
            if let settings = service.settings {
                sourceAdvisory(settings)
            }
            if let parity = service.parityWarning {
                advisory(parity, color: DS.Status.warn)
            }
            if let saveError {
                advisory(saveError, color: DS.Status.warn)
            }
            saveButton(current)
        }
        .padding(.vertical, 14)
    }

    @ViewBuilder
    private func gateLine(_ current: BudgetDraft) -> some View {
        if let values = current.parsed {
            let gate = BudgetGate.derive(
                dailyUSD: values.daily,
                windowHours: values.hours,
                skipAtPct: values.pct
            )
            Text(String(
                format: "%dh window → $%.2f budget · sessions skip at $%.2f",
                values.hours, gate.windowBudgetUSD, gate.skipThresholdUSD
            ))
            .font(DS.mono(11.5))
            .foregroundStyle(DS.Ink.p2)
        }
    }

    /// The line that makes the invisible case visible. A vault with no budget
    /// block gates at $8.34 per 5h window on the engine's defaults, which is
    /// roughly two sessions — and nothing in the app said so before this.
    @ViewBuilder
    private func sourceAdvisory(_ settings: BudgetSettings) -> some View {
        switch settings.source {
        case .defaults:
            advisory(
                String(
                    format: "No budget block in scout-config.yaml — running on engine defaults, "
                        + "so sessions skip at $%.2f. Save to write the values above.",
                    settings.skipThresholdUSD
                ),
                color: DS.Status.warn
            )
        case .legacy:
            advisory(
                "These values come from the older plan:/thresholds: keys. Saving rewrites them "
                    + "as the canonical budget: block.",
                color: DS.Ink.p3
            )
        case .vault:
            Text(settings.configPath)
                .font(DS.mono(10.5))
                .foregroundStyle(DS.Ink.p4)
        }
    }

    @ViewBuilder
    private func saveButton(_ current: BudgetDraft) -> some View {
        let unchanged = service.settings.map { BudgetDraft(from: $0) == current } ?? false
        let disabled = isSaving || current.parsed == nil || unchanged
        Button {
            Task { await save(current) }
        } label: {
            Text(isSaving ? "Saving…" : "Save")
                .font(DS.sans(12.5, weight: .medium))
                .foregroundStyle(disabled ? DS.Ink.p4 : .white)
                .padding(.horizontal, 14)
                .frame(height: 28)
                .background(
                    RoundedRectangle(cornerRadius: 6)
                        .fill(disabled ? DS.Paper.sunk : DS.Accent.fill)
                        .overlay(RoundedRectangle(cornerRadius: 6)
                            .strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                )
        }
        .buttonStyle(.plainHit)
        .disabled(disabled)
        .padding(.top, 2)
    }

    private func advisory(_ text: String, color: Color) -> some View {
        Text(text)
            .font(DS.sans(11.5))
            .foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func message(_ text: String, color: Color) -> some View {
        Text(text)
            .font(DS.sans(12.5))
            .foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.vertical, 14)
    }

    // MARK: - Actions

    private func load() async {
        guard draft == nil else { return }
        do {
            try await service.load()
            loadError = nil
            if let settings = service.settings {
                draft = BudgetDraft(from: settings)
            }
        } catch {
            loadError = "Could not read the budget configuration: \(error.localizedDescription)"
        }
    }

    private func save(_ current: BudgetDraft) async {
        guard let values = current.parsed else { return }
        isSaving = true
        defer { isSaving = false }
        do {
            try await service.save(
                dailyUSD: values.daily,
                windowHours: values.hours,
                skipAtPct: values.pct,
                failureBackoffMinutes: values.backoff
            )
            saveError = nil
            if let settings = service.settings {
                draft = BudgetDraft(from: settings)
            }
        } catch {
            saveError = error.localizedDescription
        }
    }
}
