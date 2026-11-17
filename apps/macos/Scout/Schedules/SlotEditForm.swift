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

    @State private var draft: SlotDraft
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
        onDirtyChange: @escaping (Bool) -> Void = { _ in }
    ) {
        self.liveSlot = liveSlot
        self.isNewDraft = isNewDraft
        self.onSave = onSave
        self.onDelete = onDelete
        self.onFireNow = onFireNow
        self.onRevertNewDraft = onRevertNewDraft
        self.defaultModel = defaultModel
        self.onDirtyChange = onDirtyChange
        _draft = State(initialValue: SlotDraft(from: liveSlot))
    }

    // MARK: - Static helpers

    nonisolated static func requiresTypeChangeConfirmation(draft: SlotDraft, live: Slot) -> Bool {
        draft.type != live.type
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
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
        .onChange(of: draft) { _, newDraft in onDirtyChange(newDraft.isDirty(against: liveSlot)) }
        .onChange(of: liveSlot) { _, newLive in onDirtyChange(draft.isDirty(against: newLive)) }
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
                Text("Name")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
                TextField("new-slot-1", text: $draft.key)
                    .textFieldStyle(.roundedBorder)
                if let err = SlotDraft.validateSlotKey(draft.key) {
                    Text(err).font(.caption).foregroundStyle(.red)
                } else {
                    Text("Lowercase letters, digits and dashes, like evening-research. You can't rename a schedule after you save it.")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else {
            HStack {
                Text(DisplayName.slotKey(draft.key)).font(.body.weight(.medium))
                Text(draft.key).font(.caption.monospaced()).foregroundStyle(.secondary)
                    .help("This schedule's key. It can't be changed after the first save.")
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
            Text("If the Mac was asleep")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
            EditorialSegmentedControl(
                selection: $draft.onMiss,
                options: [
                    (OnMissPolicy.fire.displayName, OnMissPolicy.fire),
                    (OnMissPolicy.skip.displayName, OnMissPolicy.skip),
                    (OnMissPolicy.collapse.displayName, OnMissPolicy.collapse),
                ],
                minSegmentWidth: 70
            )
            Text(draft.onMiss.explanation)
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var cooldownSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Wait between runs (minutes)")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
            Stepper(value: $draft.cooldownMinutes, in: 0...720, step: 15) {
                Text("\(draft.cooldownMinutes)")
            }
            .frame(width: 200)
            Text("After this schedule runs, it won't run again for at least this long.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
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
            Text("Cheaper models use less of your plan but can miss things Opus catches. Try one on a light schedule first.")
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
                Text("Script")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
                TextField("run-scout.sh", text: $draft.runner)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 240)
                Text("The engine script this schedule starts. Leave it as it is unless you know you need another.")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Catch-up window (hours)")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
                Stepper(value: $draft.missedWindowHours, in: 1...12) {
                    Text("\(draft.missedWindowHours)")
                }
                .frame(width: 200)
                Text("How late a missed run may still start after the Mac wakes.")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Kind of work")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
                AdaptiveSegmentedControl(
                    selection: $draft.type,
                    options: SlotType.allCases.map { ($0.displayName, $0) },
                    accessibilityTitle: "Kind of work"
                )
            }
            // No Runtime row: every slot runs locally, so there is nothing to
            // choose. The disabled "Remote (Plan 7)" placeholder is gone (card C3).
        }
        .padding(.top, 4)
    }

    @ViewBuilder
    private var actionBar: some View {
        HStack {
            if !isNewDraft {
                Button("Delete") { isConfirmingDelete = true }
                    .foregroundStyle(.red)
                Button("Run now") {
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
            "Change the kind of work?",
            isPresented: $isConfirmingTypeChange,
            actions: {
                Button("Cancel", role: .cancel) { }
                Button("Change") {
                    Task { await performSave() }
                }
            },
            message: {
                Text("This changes which connected tools the run needs, and which schedule goes first when two are due at the same time.")
            }
        )
        .confirmationDialog(
            "Delete \(DisplayName.slotKey(draft.key))?",
            isPresented: $isConfirmingDelete,
            titleVisibility: .visible,
            actions: {
                Button("Delete", role: .destructive) {
                    Task { await onDelete() }
                }
                Button("Cancel", role: .cancel) { }
            },
            message: {
                Text("Scout stops running it. Past runs stay in the run history.")
            }
        )
    }
}
