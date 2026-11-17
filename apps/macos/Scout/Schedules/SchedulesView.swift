import SwiftUI

struct SchedulesView: View {
    @EnvironmentObject var service: ScheduleEditService
    @EnvironmentObject var appState: AppState
    @EnvironmentObject var modelSettings: ModelSettingsService
    @EnvironmentObject var vaultUndo: VaultUndoController
    @Environment(\.undoManager) private var undoManager

    @SceneStorage("schedulesView") private var viewMode: SchedulesViewMode = .table
    @State private var filterMode: SchedulesFilterMode = .all
    @State private var selectedSlotKey: String?
    @State private var newDraftSlot: Slot?

    @State private var staleBannerVisible = false
    @State private var stalenessDetail: String?
    @State private var errorMessage: String?
    @State private var isInitialLoading = true
    /// The page's width, for `DetailPaneLayout` (UI audit C6).
    @State private var pageWidth: CGFloat?
    /// The open slot form has unsaved edits: the pane must not close over it.
    @State private var detailIsDirty = false

    /// Whether the close button or Esc may hide the detail pane. Only a
    /// compact pane closes, and never over a new slot or unsaved edits:
    /// `SlotEditForm` keeps its draft in `@State`, so closing would drop it.
    nonisolated static func canCloseDetail(pane: DetailPaneLayout, isNewDraft: Bool, isDirty: Bool) -> Bool {
        guard case .shown(_, closable: true) = pane else { return false }
        return !isNewDraft && !isDirty
    }

    var body: some View {
        let pane = DetailPaneLayout.layout(pageWidth: pageWidth, hasSelection: detailSlot != nil)
        HStack(spacing: 0) {
            masterPane
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            if case .shown(let width, let closable) = pane {
                Divider()
                SchedulesDetailPane(
                    slot: detailSlot,
                    isNewDraft: detailIsNewDraft,
                    onSave: handleSave,
                    onDelete: handleDelete,
                    onFireNow: handleFireNow,
                    onRevertNewDraft: detailIsNewDraft ? { newDraftSlot = nil; selectedSlotKey = nil } : nil,
                    defaultModel: modelSettings.settings?.default.model,
                    onDirtyChange: { detailIsDirty = $0 }
                )
                .frame(width: width)
                .overlay(alignment: .topTrailing) {
                    if closable && Self.canCloseDetail(pane: pane, isNewDraft: detailIsNewDraft, isDirty: detailIsDirty) {
                        DetailPaneCloseButton(help: "Close slot details") { selectedSlotKey = nil }
                    }
                }
            }
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { pageWidth = $0 }
        .onChange(of: selectedSlotKey) { _, _ in detailIsDirty = false }
        .onExitCommand {
            if Self.canCloseDetail(pane: pane, isNewDraft: detailIsNewDraft, isDirty: detailIsDirty) {
                selectedSlotKey = nil
            }
        }
        .background(DS.Paper.base)
        .task { await reload() }
    }

    // MARK: - Master pane

    @ViewBuilder
    private var masterPane: some View {
        VStack(alignment: .leading, spacing: 0) {
            SchedulesHeader(
                slotCount: service.slots.count,
                typeCount: typeCount,
                viewMode: $viewMode,
                onAddSlot: addDraftSlot,
                canAddSlot: service.loadState == .loaded
            )
            Divider().background(DS.Rule.hard)

            if staleBannerVisible {
                staleBanner
            }
            if let err = errorMessage {
                errorBanner(err)
            }

            SchedulesFilterChips(filterMode: $filterMode, slots: service.slots)
            Divider().background(DS.Rule.soft)

            content
        }
    }

    /// What the master pane shows. A failed load is its own state: showing the
    /// empty state there would offer "+ Add slot" on a page that doesn't know
    /// what schedule.yaml holds.
    enum ContentState: Equatable {
        case loading
        case loadFailed(String)
        case empty
        case slots
    }

    static func contentState(
        isInitialLoading: Bool,
        loadState: ScheduleLoadState,
        hasVisibleSlots: Bool,
        hasNewDraft: Bool
    ) -> ContentState {
        if case .failed(let message) = loadState { return .loadFailed(message) }
        if isInitialLoading || loadState == .notLoaded { return .loading }
        if !hasVisibleSlots && !hasNewDraft { return .empty }
        return .slots
    }

    @ViewBuilder
    private var content: some View {
        switch Self.contentState(
            isInitialLoading: isInitialLoading,
            loadState: service.loadState,
            hasVisibleSlots: !filteredSlots.isEmpty,
            hasNewDraft: newDraftSlot != nil
        ) {
        case .loading:
            VStack {
                Spacer()
                ProgressView()
                Spacer()
            }
            .frame(maxWidth: .infinity)
        case .loadFailed(let message):
            loadFailedState(message)
        case .empty:
            emptyState
        case .slots:
            switch viewMode {
            case .table:
                SchedulesMasterTable(
                    slots: filteredSlots,
                    newDraftSlot: newDraftSlot,
                    selectedSlotKey: $selectedSlotKey
                )
            case .cards:
                SchedulesMasterCards(
                    slots: filteredSlots,
                    newDraftSlot: newDraftSlot,
                    selectedSlotKey: $selectedSlotKey
                )
            case .timeline:
                SchedulesTimelineView(
                    slots: filteredSlots,
                    selectedSlotKey: $selectedSlotKey
                )
            }
        }
    }

    @ViewBuilder
    private func loadFailedState(_ message: String) -> some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 36))
                .foregroundStyle(DS.Status.err)
            Text("Couldn't load the schedule")
                .font(DS.serif(20, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
            Text("Scout won't change the schedule until it can read it.")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
            Text(message)
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p3)
                .lineLimit(6)
                .textSelection(.enabled)
                .frame(maxWidth: 420)
            Button("Retry") { Task { await reload() } }
                .buttonStyle(.borderedProminent)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "calendar.badge.plus")
                .font(.system(size: 36))
                .foregroundStyle(DS.Ink.p4)
            Text("No schedules yet")
                .font(DS.serif(20, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
            Text("A schedule tells Scout when to run by itself, such as a briefing every weekday morning. Add one to get started.")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
            Button("+ Add schedule") { addDraftSlot() }
                .buttonStyle(.borderedProminent)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Banners

    @ViewBuilder
    private var staleBanner: some View {
        HStack(alignment: .firstTextBaseline) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(DS.Status.warn)
            VStack(alignment: .leading, spacing: 2) {
                Text("The schedule changed outside the app")
                    .font(DS.sans(13, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                if let detail = stalenessDetail {
                    Text(detail).font(DS.sans(11)).foregroundStyle(DS.Ink.p3)
                }
            }
            Spacer()
            Button("Reload now") {
                Task { await reload(); staleBannerVisible = false }
            }
            .buttonStyle(.borderedProminent)
            Button("Dismiss") { staleBannerVisible = false }
                .buttonStyle(.plainHit)
                .foregroundStyle(DS.Ink.p3)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 10)
        .background(DS.Status.warn.opacity(0.15))
    }

    @ViewBuilder
    private func errorBanner(_ text: String) -> some View {
        HStack {
            Image(systemName: "xmark.octagon.fill").foregroundStyle(DS.Status.err)
            Text(text).font(DS.sans(13)).foregroundStyle(DS.Ink.p1)
            Spacer()
            Button("Dismiss") { errorMessage = nil }
                .buttonStyle(.plainHit)
                .foregroundStyle(DS.Ink.p3)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 10)
        .background(DS.Status.err.opacity(0.12))
    }

    // MARK: - Derived values

    private var filteredSlots: [Slot] {
        filterMode.apply(to: service.slots)
    }

    private var typeCount: Int {
        Set(service.slots.map(\.type)).count
    }

    private var detailSlot: Slot? {
        if let key = selectedSlotKey {
            if let draft = newDraftSlot, draft.key == key { return draft }
            return service.slots.first { $0.key == key }
        }
        return nil
    }

    private var detailIsNewDraft: Bool {
        if let key = selectedSlotKey, let draft = newDraftSlot, draft.key == key {
            return true
        }
        return false
    }

    // MARK: - Static helpers (preserved from Plan 6 — tested in SchedulesViewTests)

    static func nextNewSlotKey(existing: [String]) -> String {
        var n = 1
        while existing.contains("new-slot-\(n)") { n += 1 }
        return "new-slot-\(n)"
    }

    static func makeNewDraftSlot(key: String) -> Slot {
        Slot(
            key: key,
            type: .briefing,
            runner: "run-scout.sh",
            firesAtLocal: "09:00",
            weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
            missedWindowHours: 4,
            onMiss: .fire,
            cooldownMinutes: 60,
            budgetUsd: nil,
            tz: nil,
            runtime: .local
        )
    }

    // MARK: - Actions

    private func addDraftSlot() {
        guard service.loadState == .loaded else { return }
        guard newDraftSlot == nil else {
            selectedSlotKey = newDraftSlot?.key
            return
        }
        let existing = service.slots.map(\.key)
        let key = Self.nextNewSlotKey(existing: existing)
        let draft = Self.makeNewDraftSlot(key: key)
        newDraftSlot = draft
        selectedSlotKey = key
    }

    private func reload() async {
        // Best effort: an engine without `scoutctl model` leaves the settings
        // nil and the model picker hidden. Never blocks the schedule load.
        try? await modelSettings.load()
        // A failure lands in `service.loadState`, which the master pane
        // shows as the load-failed state (with Retry) instead of a banner.
        try? await service.loadAll()
        isInitialLoading = false
    }

    private func handleSave(_ saved: Slot) async {
        if detailIsNewDraft {
            await saveNewDraft(saved)
        } else {
            guard let key = selectedSlotKey,
                  let original = service.slots.first(where: { $0.key == key }) else { return }
            await saveExistingSlot(saved, original: original)
        }
    }

    private func saveNewDraft(_ slot: Slot) async {
        do {
            let change = try await service.update(actionName: "Add Slot") { $0 + [slot] }
            newDraftSlot = nil
            selectedSlotKey = slot.key
            registerUndo(change)
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = Self.problemText(error, action: "add the slot")
        }
    }

    /// Shared write-problem wording (C10) for the error banner.
    static func problemText(_ error: Error, action: String) -> String {
        let problem = WriteProblem.from(error, action: action)
        guard let details = problem.details else { return "\(problem.title). \(problem.message)" }
        return "\(problem.title). \(problem.message)\n\(details)"
    }

    private func saveExistingSlot(_ saved: Slot, original: Slot) async {
        do {
            registerUndo(try await service.update(actionName: "Edit Slot") { slots in
                slots.map { $0.key == original.key ? saved : $0 }
            })
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = Self.problemText(error, action: "save the slot")
        }
    }

    private func handleDelete() async {
        guard let key = selectedSlotKey,
              let slot = service.slots.first(where: { $0.key == key }) else { return }
        do {
            let change = try await service.delete(slotKey: slot.key)
            if selectedSlotKey == slot.key { selectedSlotKey = nil }
            registerUndo(change)
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = Self.problemText(error, action: "delete the slot")
        }
    }

    /// Edit ▸ Undo for a schedule save: restores the schedule file's bytes,
    /// refused if it changed since (C10). The service reloads either way.
    private func registerUndo(_ change: VaultChange?) {
        guard let change else { return }
        let service = service
        vaultUndo.register(
            .restoring(change) { change, direction in try await service.restore(change, direction) },
            with: undoManager
        ) {}
    }

    private func handleFireNow(_ key: String) async {
        await appState.fireNow(slotKey: key, bypassBudget: false)
    }
}
