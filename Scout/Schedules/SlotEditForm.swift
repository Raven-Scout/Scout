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
        onRevertNewDraft: (() -> Void)? = nil
    ) {
        self.liveSlot = liveSlot
        self.isNewDraft = isNewDraft
        self.onSave = onSave
        self.onDelete = onDelete
        self.onFireNow = onFireNow
        self.onRevertNewDraft = onRevertNewDraft
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
            DisclosureGroup("Advanced") { advancedSection }.padding(.top, 8)
            actionBar
        }
        .padding(20)
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
                    Toggle(day, isOn: Binding(
                        get: { draft.weekdays.contains(day) },
                        set: { on in
                            if on { draft.weekdays.insert(day) } else { draft.weekdays.remove(day) }
                        }
                    ))
                    .toggleStyle(.button)
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
            Picker("", selection: $draft.onMiss) {
                Text("Fire").tag(OnMissPolicy.fire)
                Text("Skip").tag(OnMissPolicy.skip)
                Text("Collapse").tag(OnMissPolicy.collapse)
            }
            .pickerStyle(.segmented)
            .frame(width: 240)
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
                Picker("", selection: $draft.type) {
                    ForEach(SlotType.allCases, id: \.self) { t in
                        Text(t.rawValue.capitalized).tag(t)
                    }
                }
                .pickerStyle(.segmented)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Runtime")
                    .font(DS.sans(11, weight: .medium))
                    .tracking(1)
                    .textCase(.uppercase)
                    .foregroundStyle(DS.Ink.p3)
                Picker("", selection: $draft.runtime) {
                    Text("Local").tag(SlotRuntime.local)
                    Text("Remote (Plan 7)").tag(SlotRuntime.remote)
                }
                .pickerStyle(.segmented)
                .disabled(true)
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
            .buttonStyle(.plain)
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
