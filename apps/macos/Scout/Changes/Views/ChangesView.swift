import SwiftUI

/// The Changes tab: what Scout changed about itself and whether it helped. It replaces the
/// Proposals and Wishlist tabs.
///
/// Top to bottom: the banners, the digest card, then the slots the next tasks fill (the "needs
/// your OK" strip, the engagement trend and the change list). Everything is read through
/// `ChangesService`; the owner's decisions go through `ChangesDecisions` and never touch a file.
struct ChangesView: View {
    @EnvironmentObject var service: ChangesService
    @EnvironmentObject var decisions: ChangesDecisions

    static let unsupportedMessage = "Update Scout's engine to see this page."
    static let unverifiedBanner = "Scout couldn't check these changes against git just now, so they're shown unconfirmed and Undo is off."
    static let calibratingBanner = "Calibrating: verdicts are shown, but nothing acts on them yet."

    var body: some View {
        ScrollView {
            // A VStack, not a LazyVStack: see ProposalsView (#83), the same height-estimation loop.
            VStack(alignment: .leading, spacing: 16) {
                header
                content
            }
            .frame(maxWidth: 920, alignment: .leading)
            .padding(.horizontal, 42)
            .padding(.top, 28)
            .padding(.bottom, 64)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .scrollIndicators(.visible)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Paper.base)
        .task {
            // AppState reads at launch; this covers a window opened before the engine was ready.
            if case .idle = service.state { await service.refresh() }
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Changes")
                .dsFont(.display, .serif, weight: .medium)
                .foregroundStyle(DS.Ink.p1)
            Text("What Scout changed about how it works, and whether each change helped.")
                .font(DS.font(.body))
                .foregroundStyle(DS.Ink.p3)
        }
        .padding(.bottom, 6)
        .overlay(alignment: .bottom) { EditorialRule() }
    }

    // MARK: - Content

    @ViewBuilder
    private var content: some View {
        switch service.state {
        case .idle, .loading:
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.top, 60)
        case .unsupported:
            Text(Self.unsupportedMessage)
                .font(DS.font(.body))
                .foregroundStyle(DS.Ink.p2)
                .padding(.top, 24)
        case .failed(let message):
            VStack(alignment: .leading, spacing: 10) {
                Text(message)
                    .font(DS.font(.body))
                    .foregroundStyle(DS.Status.err)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Retry") { Task { await service.refresh() } }
                    .buttonStyle(.ds(.primary, size: .small))
            }
            .padding(.top, 24)
        case .loaded(let snapshot):
            loaded(snapshot)
        }
    }

    @ViewBuilder
    private func loaded(_ snapshot: ChangesSnapshot) -> some View {
        ForEach(Self.bannerTitles(for: snapshot.changes), id: \.self) { title in
            DSBanner(tone: title == Self.unverifiedBanner ? .warning : .info, title: title)
        }
        DigestCard(digest: snapshot.digest)
        // Slots for the tasks that follow, top to bottom:
        //   the needs-your-OK strip, the engagement trend, the change list.
    }

    // MARK: - Pure rules

    /// The banners, in the order they show: unverified first, then calibrating.
    static func bannerTitles(for changes: ChangesDocument) -> [String] {
        var titles: [String] = []
        if !changes.verified { titles.append(unverifiedBanner) }
        if changes.calibrating { titles.append(calibratingBanner) }
        return titles
    }

    /// The sidebar badge: governance changes awaiting your OK plus suspect changes not yet undone.
    /// Counted only from a loaded snapshot; any other state shows no badge.
    static func needsYouCount(_ state: ChangesService.State) -> Int {
        guard case .loaded(let snapshot) = state else { return 0 }
        let awaiting = snapshot.changes.needsOK.filter { $0.state == "awaiting-ok" }.count
        let suspect = snapshot.changes.changes.filter { isSuspectAndNotUndone($0.test) }.count
        return awaiting + suspect
    }

    static func isSuspectAndNotUndone(_ test: ChangeTest) -> Bool {
        test.suspect && test.rolledBackBy == nil
    }
}
