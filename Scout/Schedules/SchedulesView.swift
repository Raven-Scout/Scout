import SwiftUI

struct SchedulesView: View {
    @EnvironmentObject var service: ScheduleEditService
    @EnvironmentObject var appState: AppState

    @SceneStorage("schedulesView") private var viewMode: SchedulesViewMode = .table
    @State private var filterMode: SchedulesFilterMode = .all
    @State private var selectedSlotKey: String?
    @State private var newDraftSlot: Slot?

    @State private var staleBannerVisible = false
    @State private var stalenessDetail: String?
    @State private var errorMessage: String?
    @State private var isInitialLoading = true

    var body: some View {
        NavigationSplitView {
            masterPane
                .navigationSplitViewColumnWidth(ideal: 720, max: .infinity)
        } detail: {
            SchedulesDetailPane(
                slot: detailSlot,
                isNewDraft: detailIsNewDraft,
                onSave: handleSave,
                onDelete: handleDelete,
                onFireNow: handleFireNow,
                onRevertNewDraft: detailIsNewDraft ? { newDraftSlot = nil; selectedSlotKey = nil } : nil
            )
            .navigationSplitViewColumnWidth(min: 360, ideal: 420)
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
                onAddSlot: addDraftSlot
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

    @ViewBuilder
    private var content: some View {
        if isInitialLoading {
            VStack {
                Spacer()
                ProgressView()
                Spacer()
            }
            .frame(maxWidth: .infinity)
        } else if filteredSlots.isEmpty && newDraftSlot == nil {
            emptyState
        } else {
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
                timelinePlaceholder
            }
        }
    }

    private var timelinePlaceholder: some View {
        VStack(spacing: 8) {
            Spacer()
            Image(systemName: "clock.arrow.circlepath")
                .font(.system(size: 32))
                .foregroundStyle(DS.Ink.p4)
            Text("Timeline view coming in a future plan")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
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
            Text("No scheduled slots")
                .font(DS.serif(20, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
            Text("Add a slot to start scheduling Scout runs. Or run `scoutctl schedule init` from the terminal to seed the plugin defaults (10 standard slots).")
                .font(DS.sans(13))
                .foregroundStyle(DS.Ink.p3)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
            Button("+ Add slot") { addDraftSlot() }
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
                Text("schedule.yaml was modified externally")
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
                .buttonStyle(.plain)
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
                .buttonStyle(.plain)
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
        do {
            try await service.loadAll()
            isInitialLoading = false
        } catch {
            errorMessage = error.localizedDescription
            isInitialLoading = false
        }
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
        var combined = service.slots
        combined.append(slot)
        do {
            try await service.save(allSlots: combined)
            newDraftSlot = nil
            selectedSlotKey = slot.key
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func saveExistingSlot(_ saved: Slot, original: Slot) async {
        var updated = service.slots
        if let idx = updated.firstIndex(where: { $0.key == original.key }) {
            updated[idx] = saved
        }
        do {
            try await service.save(allSlots: updated)
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func handleDelete() async {
        guard let key = selectedSlotKey,
              let slot = service.slots.first(where: { $0.key == key }) else { return }
        do {
            try await service.delete(slotKey: slot.key)
            if selectedSlotKey == slot.key { selectedSlotKey = nil }
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func handleFireNow(_ key: String) async {
        await appState.fireNow(slotKey: key, bypassBudget: false)
    }
}
