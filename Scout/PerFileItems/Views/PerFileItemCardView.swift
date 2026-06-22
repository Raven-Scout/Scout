import SwiftUI

/// One per-file Wishlist/Research item rendered as an editorial card: header
/// (date chip + title + priority pill + status pill), optional source/area
/// line, markdown body, and — for active items — Done / Drop actions. Owns
/// its in-flight + error state so a slow or failed write surfaces on the card.
struct PerFileItemCardView: View {
    let item: PerFileItem
    /// Display label for the optional source/area field (e.g. "Source", "Area").
    let optionalLabel: String?
    /// Performs the write. Throws so the card can show an inline error.
    let onResolve: @MainActor (ItemResolution) async throws -> Void

    @State private var inFlight: ItemResolution?
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
            if item.isActive {
                actions
            }
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
                    Text(item.date)
                        .font(DS.mono(11))
                        .foregroundStyle(DS.Ink.p4)
                }
                Text(item.title)
                    .font(DS.serif(17, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            ItemPriorityPill(priority: item.priority)
            ItemStatusPill(status: item.status)
        }
    }

    // MARK: - Actions

    private var optionalValue: String? { item.source ?? item.area }

    private var actions: some View {
        HStack(spacing: 6) {
            resolveButton("Done", systemImage: "checkmark", resolution: .done, primary: true)
            resolveButton("Drop", systemImage: "xmark", resolution: .dropped, primary: false)
            Spacer(minLength: 0)
        }
        .padding(.top, 2)
    }

    @ViewBuilder
    private func resolveButton(
        _ label: String,
        systemImage: String,
        resolution: ItemResolution,
        primary: Bool
    ) -> some View {
        let isBusy = inFlight == resolution
        Button { resolve(resolution) } label: {
            HStack(spacing: 5) {
                if isBusy {
                    ProgressView().controlSize(.small).frame(width: 12, height: 12)
                } else {
                    Image(systemName: systemImage).font(.system(size: 10))
                }
                Text(label).font(DS.sans(11.5, weight: .medium))
            }
            .foregroundStyle(primary ? DS.Status.ok : DS.Ink.p3)
            .padding(.horizontal, 12)
            .frame(height: 26)
            .background {
                RoundedRectangle(cornerRadius: 5)
                    .fill(DS.Paper.raised)
                    .overlay(
                        RoundedRectangle(cornerRadius: 5)
                            .strokeBorder(primary ? DS.Status.ok.opacity(0.4) : DS.Rule.hard, lineWidth: 0.5)
                    )
            }
        }
        .buttonStyle(.plainHit)
        .disabled(inFlight != nil)
        .onHover { hovering in
            if hovering, inFlight == nil { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }

    private func resolve(_ resolution: ItemResolution) {
        inFlight = resolution
        errorText = nil
        Task {
            do {
                try await onResolve(resolution)
            } catch {
                errorText = "Couldn't update the file — \(error.localizedDescription)"
            }
            inFlight = nil
        }
    }
}
