import SwiftUI

/// Inline expanded edit form for a single slot. Holds a SlotDraft in @State,
/// validates per-field live, and exposes Save / Delete / Fire-now / Revert
/// buttons wired to caller-provided async callbacks.
@MainActor
struct SlotEditForm: View {
    let liveSlot: Slot
    let isNewDraft: Bool

    let onSave: (Slot) async -> Void
    let onDelete: () async -> Void
    let onFireNow: (String) async -> Void
    let onRevertNewDraft: (() -> Void)?
    /// nil hides the model picker: the engine can't run per-slot models.
    let defaultModel: String?
    /// Told whether the draft differs from `liveSlot`, so the page can refuse
    /// to close the pane over unsaved edits (UI audit C6).
    let onDirtyChange: (Bool) -> Void
    /// Where unsaved edits outlive this view: the page tears the form down on
    /// every slot switch (`.id(slot.key)`), so `@State` alone would drop them.
    private let drafts: SlotDraftStore

    @State private var draft: SlotDraft
    /// Fields changed both here and in schedule.yaml since the draft was
    /// typed; the form keeps the user's value and says so.
    @State private var conflictingFields: [String]
    @State private var isConfirmingTypeChange = false
    @State private var isConfirmingDelete = false
    @State private var isSaving = false

    init(
        liveSlot: Slot,
        isNewDraft: Bool = false,
        onSave: @escaping (Slot) async -> Void,
        onDelete: @escaping () async -> Void,
        onFireNow: @escaping (String) async -> Void,
        onRevertNewDraft: (() -> Void)? = nil,
        defaultModel: String? = nil,
        onDirtyChange: @escaping (Bool) -> Void = { _ in },
        drafts: SlotDraftStore = .shared
    ) {
        self.liveSlot = liveSlot
        self.isNewDraft = isNewDraft
        self.onSave = onSave
        self.onDelete = onDelete
        self.onFireNow = onFireNow
        self.onRevertNewDraft = onRevertNewDraft
        self.defaultModel = defaultModel
        self.onDirtyChange = onDirtyChange
        self.drafts = drafts
        let initial = Self.initialDraft(live: liveSlot, stashed: drafts.entry(for: liveSlot.key))
        _draft = State(initialValue: initial.draft)
        _conflictingFields = State(initialValue: initial.conflicts)
    }

    // MARK: - Static helpers

    nonisolated static func requiresTypeChangeConfirmation(draft: SlotDraft, live: Slot) -> Bool {
        draft.type != live.type
    }

    /// What the form opens with: the slot's unsaved draft if there is one,
    /// rebased onto the slot as it is now, else the slot itself. Pure, so
    /// tests can pin it.
    nonisolated static func initialDraft(live: Slot, stashed: SlotFormDraft?) -> (draft: SlotDraft, conflicts: [String]) {
        guard let stashed, stashed.isDirty else { return (SlotDraft(from: live), []) }
        return stashed.draft.rebased(from: stashed.baseline, onto: live)
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !conflictingFields.isEmpty {
                conflictNote
            }
            slotKeyField
            timeAndWeekdaysSection
            onMissSection
            cooldownSection
            if let defaultModel {
                modelSection(defaultModel: defaultModel)
            }
            agentSection
            DisclosureGroup("Advanced") { advancedSection }.padding(.top, 8)
            actionBar
        }
        .padding(20)
        .onAppear { onDirtyChange(draft.isDirty(against: liveSlot)) }
        .onChange(of: draft) { _, newDraft in
            drafts.update(newDraft, baseline: liveSlot, for: liveSlot.key)
            onDirtyChange(newDraft.isDirty(against: liveSlot))
        }
        .onChange(of: liveSlot) { oldLive, newLive in
            // The slot changed under the form (a save, undo, reload): keep
            // the user's edits, take the new values for everything else.
            let rebased = draft.rebased(from: oldLive, onto: newLive)
            conflictingFields = rebased.draft.isDirty(against: newLive) ? rebased.conflicts : []
            draft = rebased.draft
            drafts.update(rebased.draft, baseline: newLive, for: newLive.key)
            onDirtyChange(rebased.draft.isDirty(against: newLive))
        }
    }

    private var conflictNote: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(DS.Status.warn)
            Text("schedule.yaml changed \(conflictingFields.joined(separator: ", ")) since you edited this slot. The form shows your values; Save writes them, Revert takes the file's.")
                .font(DS.sans(12))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: - Private helpers

    private func performSave() async {
        isSaving = true
        defer { isSaving = false }
        await onSave(draft.toSlot())
    }

    // MARK: - Subviews

    @ViewBuilder
    private var slotKeyField: some View {
        if isNewDraft {
            VStack(alignment: .leading, spacing: 4) {
                Text("Slot key")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
                TextField("new-slot-1", text: $draft.key)
                    .textFieldStyle(.roundedBorder)
                if let err = SlotDraft.validateSlotKey(draft.key) {
                    Text(err).font(.caption).foregroundStyle(.red)
                } else {
                    Text("Slot keys are immutable after first save. Choose carefully.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        } else {
            HStack {
                Image(systemName: "lock.fill").foregroundStyle(.secondary)
                Text(draft.key).font(.body.monospaced())
                Spacer()
            }
        }
    }

    @ViewBuilder
    private var timeAndWeekdaysSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Time")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
            TextField("HH:MM", text: $draft.firesAtLocal)
                .textFieldStyle(.roundedBorder)
                .frame(width: 120)
            if let err = SlotDraft.validateFiresAtLocal(draft.firesAtLocal) {
                Text(err).font(.caption).foregroundStyle(.red)
            }
        }
        VStack(alignment: .leading, spacing: 4) {
            Text("Weekdays")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
            HStack(spacing: 4) {
                ForEach(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], id: \.self) { day in
                    let isOn = draft.weekdays.contains(day)
                    Button {
                        if isOn { draft.weekdays.remove(day) } else { draft.weekdays.insert(day) }
                    } label: {
                        Text(day)
                            .font(DS.sans(13, weight: .medium))
                            .lineLimit(1)
                            .fixedSize(horizontal: true, vertical: false)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 5)
                            .frame(minWidth: 44)
                            .background(
                                RoundedRectangle(cornerRadius: 7)
                                    .fill(isOn ? DS.Ink.p1 : DS.Paper.raised)
                                    .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                            )
                            .foregroundStyle(isOn ? DS.Paper.base : DS.Ink.p2)
                    }
                    .buttonStyle(.plainHit)
                }
            }
            if let err = SlotDraft.validateWeekdays(Array(draft.weekdays)) {
                Text(err).font(.caption).foregroundStyle(.red)
            }
        }
    }

    @ViewBuilder
    private var onMissSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("On miss")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
            EditorialSegmentedControl(
                selection: $draft.onMiss,
                options: [
                    ("Fire", OnMissPolicy.fire),
                    ("Skip", OnMissPolicy.skip),
                    ("Collapse", OnMissPolicy.collapse),
                ],
                minSegmentWidth: 70
            )
        }
    }

    @ViewBuilder
    private var cooldownSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Cooldown (minutes)")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
            Stepper(value: $draft.cooldownMinutes, in: 0...720, step: 15) {
                Text("\(draft.cooldownMinutes)")
            }
            .frame(width: 200)
        }
    }

    private func modelSection(defaultModel: String) -> some View {
        let current = ModelChoice(model: draft.model)
        return VStack(alignment: .leading, spacing: 4) {
            Text("Model")
                .font(DS.sans(11, weight: .medium))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(DS.Ink.p3)
            AdaptiveSegmentedControl(
                selection: Binding(get: { current }, set: { draft.model = $0.model }),
                options: ModelChoice.slotOptions(defaultModel: defaultModel, current: current),
                minSegmentWidth: 64,
                accessibilityTitle: "Model"
            )
            Text("Cheaper models use less of your plan but can miss things Opus catches. Try one on a light slot first.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Read-only until Phase 2 adds an agent picker. The value round-trips
    /// through `draft` untouched, so saving never changes it.
    private var agentSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Agent")
                .font(DS.sans(11, weight: .medium))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(DS.Ink.p3)
            Text(liveSlot.agentRuntimeDisplayName.map { "Runs on \($0)" }
                 ?? "Runs on Scout's default agent")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p2)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var advancedSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Runner")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
                TextField("run-scout.sh", text: $draft.runner)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 240)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Missed window (hours)")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
                Stepper(value: $draft.missedWindowHours, in: 1...12) {
                    Text("\(draft.missedWindowHours)")
                }
                .frame(width: 200)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Type")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
                AdaptiveSegmentedControl(
                    selection: $draft.type,
                    options: SlotType.allCases.map { ($0.rawValue.capitalized, $0) },
                    accessibilityTitle: "Type"
                )
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Runtime")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
                EditorialSegmentedControl(
                    selection: $draft.runtime,
                    options: [
                        ("Local", SlotRuntime.local),
                        ("Remote (Plan 7)", SlotRuntime.remote),
                    ],
                    minSegmentWidth: 110
                )
                .disabled(true)
                .opacity(0.5)
                Text("Remote slot execution arrives in Plan 7 (Anthropic routines integration).")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.top, 4)
    }

    @ViewBuilder
    private var actionBar: some View {
        HStack {
            if !isNewDraft {
                Button("Delete") { isConfirmingDelete = true }
                    .foregroundStyle(.red)
                Button("Fire now") {
                    Task { await onFireNow(draft.key) }
                }
                .disabled(draft.isDirty(against: liveSlot))
            }
            Spacer()
            Button("Revert") {
                if isNewDraft {
                    onRevertNewDraft?()
                } else {
                    draft = SlotDraft(from: liveSlot)
                    conflictingFields = []
                }
            }
            .disabled(!isNewDraft && !draft.isDirty(against: liveSlot))
            Button {
                if Self.requiresTypeChangeConfirmation(draft: draft, live: liveSlot) {
                    isConfirmingTypeChange = true
                } else {
                    Task { await performSave() }
                }
            } label: {
                Text("Save")
                    .font(DS.sans(13, weight: .medium))
                    .padding(.horizontal, 14)
                    .padding(.vertical, 6)
                    .background(DS.Accent.fill, in: RoundedRectangle(cornerRadius: 6))
                    .foregroundStyle(DS.Paper.base)
            }
            .buttonStyle(.plainHit)
            .keyboardShortcut(.return, modifiers: [.command])
            .disabled(draft.firstError != nil || (!isNewDraft && !draft.isDirty(against: liveSlot)))
        }
        .padding(.top, 8)
        .alert(
            "Change slot type?",
            isPresented: $isConfirmingTypeChange,
            actions: {
                Button("Cancel", role: .cancel) { }
                Button("Change") {
                    Task { await performSave() }
                }
            },
            message: {
                Text("Changing slot type updates which connectors are required at fire time and reorders single-fire-per-tick priority. Continue?")
            }
        )
        .confirmationDialog(
            "Delete \(draft.key)?",
            isPresented: $isConfirmingDelete,
            titleVisibility: .visible,
            actions: {
                Button("Delete", role: .destructive) {
                    Task { await onDelete() }
                }
                Button("Cancel", role: .cancel) { }
            },
            message: {
                Text("Removes this slot from schedule.yaml. Tracker history is retained but unused. Run-event logs keep their references.")
            }
        )
    }
}
