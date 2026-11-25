import SwiftUI

/// "Needs your OK": the governance changes waiting for the owner, each with a Review button that opens the
/// diff. Hidden when nothing is waiting. The sidebar badge and the digest card count the same items.
struct NeedsOKStrip: View {
    let items: [NeedsOKItem]
    let vaultDirectory: URL?
    @ObservedObject var decisions: ChangesDecisions
    @ObservedObject var service: ChangesService

    @State private var reviewing: NeedsOKItem?

    /// Only items still waiting for an answer.
    static func awaiting(_ items: [NeedsOKItem]) -> [NeedsOKItem] {
        items.filter { $0.state == "awaiting-ok" }
    }

    static func title(_ count: Int) -> String { "Needs your OK (\(count))" }

    var body: some View {
        let waiting = Self.awaiting(items)
        // The sheet hangs off a container that is always present, so it outlives the strip's own content
        // (a decision empties `waiting` while the sheet is still on screen).
        VStack(alignment: .leading, spacing: 0) {
            if !waiting.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Text(Self.title(waiting.count))
                        .dsFont(.body, .serif, weight: .semibold)
                        .foregroundStyle(DS.Attention.you)
                    ForEach(waiting) { item in row(item) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .editorialCard()
                .accessibilityElement(children: .contain)
                .accessibilityLabel(Self.title(waiting.count))
            }
        }
        .sheet(item: $reviewing) { item in
            GovernanceReviewSheet(item: item, vaultDirectory: vaultDirectory, decisions: decisions,
                                  service: service, onClose: { reviewing = nil })
        }
    }

    private func row(_ item: NeedsOKItem) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(verbatim: item.summary)
                    .font(DS.font(.label, weight: .semibold))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                if let why = item.whyLine {
                    Text(verbatim: why)
                        .font(DS.font(.caption))
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if item.governance { DSPill("Governance change", tone: .attention) }
            }
            Spacer(minLength: 8)
            Button("Review") { reviewing = item }
                .buttonStyle(.ds(.secondary, size: .small))
                .accessibilityLabel("Review: \(item.summary)")
        }
    }
}
