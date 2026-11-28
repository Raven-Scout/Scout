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
    @State private var errorProblem: Problem?
    /// A Run now from this page that didn't start (UI audit #56): the
    /// engine's words go behind Details.
    @State private var fireNowProblem: Problem?
    @State private var isInitialLoading = true
    /// The page's width, for `DetailPaneLayout` (UI audit C6).
    @State private var pageWidth: CGFloat?
    /// The open slot form has unsaved edits: the pane must not close over it.
    @State private var detailIsDirty = false
    /// Unsaved slot-form drafts outlive the form (see `SlotDraftStore`), so
    /// switching slots or leaving the page keeps them. Observed for the
    /// notice when a reload drops edits to a slot that left the schedule.
    @ObservedObject private var drafts = SlotDraftStore.shared

    /// Run health's `slot.*` findings ("hasn't run for 3 days"), by slot
    /// key. Read inside `Observing(appState.runLedgerService)`: `AppState`
    /// re-publishes none of the ledger's changes.
    private var slotWarnings: [String: [RunOutcomeDisplay.SlotWarning]] {
        RunOutcomeDisplay.slotWarnings(appState.runLedgerService.health)
    }

    /// Whether the close button or Esc may hide the detail pane. Only a
    /// compact pane closes, and never over a new slot or unsaved edits:
    /// `SlotDraftStore` would keep them, but a pane that vanishes over them
    /// leaves no sign they exist.
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
                    onRevertNewDraft: detailIsNewDraft ? { finishNewDraft(key: newDraftSlot?.key); selectedSlotKey = nil } : nil,
                    defaultModel: modelSettings.settings?.default.model,
                    onDirtyChange: { detailIsDirty = $0 },
                    existingKeys: Set(service.slots.map(\.key)),
                    firingKeys: appState.firingSlotKeys
                )
                .frame(width: width)
                .overlay(alignment: .topTrailing) {
                    if closable && Self.canCloseDetail(pane: pane, isNewDraft: detailIsNewDraft, isDirty: detailIsDirty) {
                        DetailPaneCloseButton(help: "Close schedule details") { selectedSlotKey = nil }
                    }
                }
            }
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { pageWidth = $0 }
        // The next form reports its own state on appear; until then, go by
        // whether that slot has a stored draft.
        .onChange(of: selectedSlotKey) { _, key in
            detailIsDirty = key.map { drafts.hasDraft(for: $0) } ?? false
        }
        .onExitCommand {
            if Self.canCloseDetail(pane: pane, isNewDraft: detailIsNewDraft, isDirty: detailIsDirty) {
                selectedSlotKey = nil
            }
        }
        .background(DS.Paper.base)
        .task {
            // A new slot added before leaving the page comes back with it.
            // (ScheduleEditService reconciles stored drafts on every load.)
            if newDraftSlot == nil { newDraftSlot = drafts.pendingNewSlot }
            await reload()
        }
    }

    /// The page's load-failed state: plain words, the engine's output under
    /// Details.
    static func loadFailedProblem(_ message: String) -> Problem {
        Problem(severity: .error, title: "Couldn't load the schedule",
                impact: "Scout won't change the schedule until it can read it.",
                fix: "Try again. If it keeps failing, open Settings ▸ Engine and choose Check now.",
                details: message)
    }

    /// The notice for edits a reload dropped because their slot left the
    /// schedule (deleted elsewhere, or an undone add).
    static func droppedEditsText(_ names: [String]) -> String {
        let list = names.joined(separator: ", ")
        return names.count == 1
            ? "Unsaved edits to \(list) were dropped: that schedule no longer exists."
            : "Unsaved edits to \(list) were dropped: those schedules no longer exist."
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
            if let problem = errorProblem {
                errorBanner(problem)
            }
            if let problem = fireNowProblem {
                ProblemView(problem: problem, onDismiss: { fireNowProblem = nil })
                    .padding(.horizontal, 24)
                    .padding(.vertical, 8)
            }
            if !drafts.droppedSlotNames.isEmpty {
                droppedEditsBanner
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
            case .table, .cards:
                // One list around both, so the picker keeps its focus.
                // Observing redraws only the list when the run ledger changes
                // (slot warnings), not the whole page.
                Observing(appState.runLedgerService) {
                    SchedulesMasterList(
                        cards: viewMode == .cards,
                        slots: filteredSlots,
                        newDraftSlot: newDraftSlot,
                        selectedSlotKey: $selectedSlotKey,
                        warnings: slotWarnings
                    )
                }
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
                .font(DS.symbol(36))
                .foregroundStyle(DS.Status.err)
            // scoutctl's own output goes behind Details (UI audit #56).
            ProblemView(problem: Self.loadFailedProblem(message),
                        actions: [.retry { Task { await reload() } }],
                        style: .inline)
                .frame(maxWidth: 460)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "calendar.badge.plus")
                .font(DS.symbol(36))
                .foregroundStyle(DS.Ink.p4)
            Text("No schedules yet")
                .dsFont(.title, .serif, weight: .medium)
                .foregroundStyle(DS.Ink.p2)
            Text("A schedule tells Scout when to run by itself, such as a briefing every weekday morning. Add one to get started.")
                .font(DS.font(.body))
                .foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
            Button("+ Add schedule") { addDraftSlot() }
                .buttonStyle(.dsPrimary)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Banners

    private var staleBanner: some View {
        DSBanner(tone: .warning, title: "The schedule changed outside the app",
                 message: stalenessDetail,
                 actions: [ProblemAction("Reload now") { Task { await reload(); staleBannerVisible = false } }],
                 onDismiss: { staleBannerVisible = false })
            .padding(.horizontal, 24)
            .padding(.vertical, 8)
    }

    private var droppedEditsBanner: some View {
        DSBanner(tone: .warning, title: Self.droppedEditsText(drafts.droppedSlotNames),
                 onDismiss: { drafts.clearDropped() })
            .padding(.horizontal, 24)
            .padding(.vertical, 8)
    }

    /// A failed write: the plain title and what to do, with the engine's own
    /// output (validator stderr, a system error) behind Details (R2).
    private func errorBanner(_ problem: Problem) -> some View {
        ProblemView(problem: problem, onDismiss: { errorProblem = nil })
            .padding(.horizontal, 24)
            .padding(.vertical, 8)
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
        drafts.pendingNewSlot = draft
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

    /// `baseline` is the slot as the form last saw it, which the edit was
    /// typed against (not `service.slots` now: a reload may have landed).
    private func handleSave(_ saved: Slot, baseline: Slot) async {
        if detailIsNewDraft {
            await saveNewDraft(saved)
        } else {
            await saveExistingSlot(saved, baseline: baseline)
        }
    }

    /// The new slot is done with (saved or reverted). Cleared in the store
    /// directly, by the key it had when the save started: a save that ends
    /// after the page closed must not leave it behind as an unsaved new slot
    /// that would then fail to save as a duplicate.
    private func finishNewDraft(key: String?) {
        if let key { drafts.finishNewSlot(key: key) }
        newDraftSlot = nil
    }

    private func saveNewDraft(_ slot: Slot) async {
        let draftKey = newDraftSlot?.key
        do {
            let change = try await service.update(actionName: "Add Schedule") { $0 + [slot] }
            clearStaleNotice()
            finishNewDraft(key: draftKey)
            selectedSlotKey = slot.key
            registerUndo(change)
        } catch let written as ScheduleReloadAfterWriteError {
            // The slot is in the file; only the reload failed.
            finishNewDraft(key: draftKey)
            selectedSlotKey = nil
            registerUndo(written.change)
            errorProblem = Self.problem(written, action: "add the schedule")
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorProblem = Self.problem(error, action: "add the schedule")
        }
    }

    private func clearStaleNotice() {
        staleBannerVisible = false
        stalenessDetail = nil
    }

    /// A save to a schedule that was removed outside the app.
    static func removedSlotProblem(_ key: String) -> Problem {
        Problem(severity: .warning,
                title: "\(DisplayName.slotKey(key)) was removed outside the app",
                impact: "Your edits to it weren't saved: that schedule no longer exists.")
    }

    /// Shared write-problem wording (C10) for the error banner. The details
    /// stay in `Problem.details`, which `ProblemView` shows only behind
    /// "Details", never on a visible line under the title.
    static func problem(_ error: Error, action: String) -> Problem {
        let write = WriteProblem.from(error, action: action)
        return Problem(severity: write.kind == .committedOnDiskOnly ? .warning : .error,
                       title: write.title, impact: write.message, details: write.details)
    }

    private func saveExistingSlot(_ saved: Slot, baseline: Slot) async {
        do {
            registerUndo(try await service.saveSlot(saved, baseline: baseline))
            // The save went through, so a "not saved" notice is settled.
            clearStaleNotice()
        } catch let written as ScheduleReloadAfterWriteError {
            // Saved; the next successful load settles the form's draft.
            registerUndo(written.change)
            errorProblem = Self.problem(written, action: "save the schedule")
        } catch let stale as StaleScheduleError {
            if let key = stale.removedSlot {
                // Nothing to reload into: say so, without "Reload now".
                errorProblem = Self.removedSlotProblem(key)
            } else {
                staleBannerVisible = true
                stalenessDetail = stale.errorDescription
            }
        } catch {
            errorProblem = Self.problem(error, action: "save the schedule")
        }
    }

    private func handleDelete() async {
        guard let key = selectedSlotKey,
              let slot = service.slots.first(where: { $0.key == key }) else { return }
        // The user chose to delete the slot, edits and all: set its draft
        // aside so the reload doesn't report it as dropped, and put it back
        // if the delete fails.
        let setAside = drafts.entry(for: slot.key)
        drafts.discard(for: slot.key)
        do {
            let change = try await service.delete(slotKey: slot.key)
            if selectedSlotKey == slot.key { selectedSlotKey = nil }
            registerUndo(change)
        } catch let written as ScheduleReloadAfterWriteError {
            // The slot is gone from the file; only the reload failed. Its
            // draft stays discarded: putting it back would have the next
            // load report it as dropped.
            if selectedSlotKey == slot.key { selectedSlotKey = nil }
            registerUndo(written.change)
            errorProblem = Self.problem(written, action: "delete the schedule")
        } catch let stale as StaleScheduleError {
            restoreIfStillThere(setAside, key: slot.key)
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            restoreIfStillThere(setAside, key: slot.key)
            errorProblem = Self.problem(error, action: "delete the schedule")
        }
    }

    /// Put a set-aside draft back after a delete that wrote nothing. (A
    /// delete that wrote but couldn't reload throws
    /// `ScheduleReloadAfterWriteError` and never gets here; the check is a
    /// backstop in case the slot is gone anyway.)
    private func restoreIfStillThere(_ entry: SlotFormDraft?, key: String) {
        guard service.slots.contains(where: { $0.key == key }) else { return }
        drafts.restore(entry, for: key)
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
        fireNowProblem = nil
        await appState.fireNow(slotKey: key)
        fireNowProblem = Self.fireNowProblem(appState.fireNowError, slotKey: key)
    }

    /// The banner for a Run now that didn't start, or nil when it did.
    /// `AppState.fireNowError` carries scoutctl's own words, which go
    /// behind Details, never on a visible line.
    static func fireNowProblem(_ error: String?, slotKey: String) -> Problem? {
        guard let error, !error.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        let name = DisplayName.slotKey(slotKey)
        if error.contains("already being started") {
            return Problem(severity: .info, title: "\(name) is already starting",
                           impact: "Scout ignored the second Run now.", details: error)
        }
        return Problem(severity: .error, title: "Couldn't start \(name)",
                       impact: "Scout's engine didn't start the run.",
                       fix: "Try again in a minute. If it keeps failing, open Settings ▸ Engine and choose Check now.",
                       details: error)
    }
}
