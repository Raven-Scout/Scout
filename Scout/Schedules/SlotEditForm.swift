import SwiftUI

/// Inline expanded edit form for a single slot. Holds a SlotDraft in @State,
/// validates per-field live, and exposes a Save button when (draft != live)
/// AND all fields validate. Full action wiring (onSave, onDelete, onFireNow,
/// onRevertNewDraft) lands in Task 11; this task uses no-op closures.
@MainActor
struct SlotEditForm: View {
    let liveSlot: Slot
    let isNewDraft: Bool

    @State private var draft: SlotDraft

    init(liveSlot: Slot, isNewDraft: Bool = false) {
        self.liveSlot = liveSlot
        self.isNewDraft = isNewDraft
        _draft = State(initialValue: SlotDraft(from: liveSlot))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            slotKeyField
            timeAndWeekdaysSection
            onMissSection
            cooldownSection
            DisclosureGroup("Advanced") { advancedSection }.padding(.top, 8)
            actionBar
        }
        .padding()
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.4))
        .cornerRadius(8)
    }

    @ViewBuilder
    private var slotKeyField: some View {
        if isNewDraft {
            VStack(alignment: .leading, spacing: 4) {
                Text("Slot key").font(.callout).foregroundStyle(.secondary)
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
            Text("Time").font(.callout).foregroundStyle(.secondary)
            TextField("HH:MM", text: $draft.firesAtLocal)
                .textFieldStyle(.roundedBorder)
                .frame(width: 120)
            if let err = SlotDraft.validateFiresAtLocal(draft.firesAtLocal) {
                Text(err).font(.caption).foregroundStyle(.red)
            }
        }
        VStack(alignment: .leading, spacing: 4) {
            Text("Weekdays").font(.callout).foregroundStyle(.secondary)
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
            Text("On miss").font(.callout).foregroundStyle(.secondary)
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
            Text("Cooldown (minutes)").font(.callout).foregroundStyle(.secondary)
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
                Text("Runner").font(.callout).foregroundStyle(.secondary)
                TextField("run-scout.sh", text: $draft.runner)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 240)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Missed window (hours)").font(.callout).foregroundStyle(.secondary)
                Stepper(value: $draft.missedWindowHours, in: 1...12) {
                    Text("\(draft.missedWindowHours)")
                }
                .frame(width: 200)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Type").font(.callout).foregroundStyle(.secondary)
                Picker("", selection: $draft.type) {
                    ForEach(SlotType.allCases, id: \.self) { t in
                        Text(t.rawValue.capitalized).tag(t)
                    }
                }
                .pickerStyle(.segmented)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Runtime").font(.callout).foregroundStyle(.secondary)
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
            Spacer()
            Button("Revert") {
                draft = SlotDraft(from: liveSlot)
            }
            .disabled(!draft.isDirty(against: liveSlot))
            Button("Save") {
                // Action wiring lands in Task 11.
            }
            .keyboardShortcut(.return, modifiers: [.command])
            .disabled(draft.firstError != nil || !draft.isDirty(against: liveSlot))
        }
        .padding(.top, 8)
    }
}
