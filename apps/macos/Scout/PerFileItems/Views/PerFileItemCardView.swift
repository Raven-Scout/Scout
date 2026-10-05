import SwiftUI

/// One per-file Wishlist/Research item as an editorial card. Active items can
/// change priority (pill menu), Start (→ in-progress), or resolve (Done/Drop);
/// resolved items can Reopen (issue #41). Owns its busy + error state.
struct PerFileItemCardView: View {
    let item: PerFileItem
    let optionalLabel: String?
    /// Priorities offered in the pill menu (empty → read-only pill).
    var priorityOptions: [ItemPriority] = []
    var onChangePriority: @MainActor (ItemPriority) async throws -> Void = { _ in }
    var onChangeStatus: @MainActor (ItemStatus) async throws -> Void = { _ in }
    let onResolve: @MainActor (ItemResolution) async throws -> Void

    @State private var isWriting = false
    @State private var errorText: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if let label = optionalLabel, let value = optionalValue, !value.isEmpty {
                Text("\(label): \(value)")
                    .font(DS.mono(11))
                    .foregroundStyle(DS.Ink.p4)
            }
            if !item.bodyBlocks.isEmpty {
                MarkdownBodyView(blocks: item.bodyBlocks)
            }
            actions
            if let errorText {
                Label(errorText, systemImage: "exclamationmark.triangle.fill")
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Status.err)
            }
        }
        .editorialCard(padding: 18)
    }

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                if !item.date.isEmpty {
                    Text(item.date).font(DS.mono(11)).foregroundStyle(DS.Ink.p4)
                }
                Text(item.title)
                    .font(DS.serif(17, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            if item.isActive && !priorityOptions.isEmpty {
                ItemPriorityPill(priority: item.priority, options: priorityOptions) { newPriority in
                    perform { try await onChangePriority(newPriority) }
                }
                .disabled(isWriting)
            } else {
                ItemPriorityPill(priority: item.priority)
            }
            ItemStatusPill(status: item.status)
        }
    }

    private var optionalValue: String? { item.source ?? item.area }

    // MARK: - Actions

    @ViewBuilder
    private var actions: some View {
        HStack(spacing: 6) {
            if item.isActive {
                if item.status == .open {
                    actionButton("Start", systemImage: "play.fill", tint: DS.Ink.p2) {
                        try await onChangeStatus(.inProgress)
                    }
                }
                actionButton("Done", systemImage: "checkmark", tint: DS.Status.ok) {
                    try await onResolve(.done)
                }
                actionButton("Drop", systemImage: "xmark", tint: DS.Ink.p3) {
                    try await onResolve(.dropped)
                }
            } else {
                actionButton("Reopen", systemImage: "arrow.uturn.backward", tint: DS.Ink.p2) {
                    try await onChangeStatus(.open)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.top, 2)
    }

    @ViewBuilder
    private func actionButton(_ label: String, systemImage: String, tint: Color,
                              _ op: @escaping @MainActor () async throws -> Void) -> some View {
        Button { perform(op) } label: {
            HStack(spacing: 5) {
                Image(systemName: systemImage).font(.system(size: 10))
                Text(label).font(DS.sans(11.5, weight: .medium))
            }
            .foregroundStyle(tint)
            .padding(.horizontal, 12)
            .frame(height: 26)
            .background {
                RoundedRectangle(cornerRadius: 5)
                    .fill(DS.Paper.raised)
                    .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(DS.Rule.hard, lineWidth: 0.5))
            }
        }
        .buttonStyle(.plainHit)
        .disabled(isWriting)
        .onHover { hovering in
            if hovering, !isWriting { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }

    private func perform(_ op: @escaping @MainActor () async throws -> Void) {
        isWriting = true
        errorText = nil
        Task {
            do { try await op() }
            catch { errorText = "Couldn't update the file — \(error.localizedDescription)" }
            isWriting = false
        }
    }
}
