import SwiftUI

struct MenuBarIcon: View {
    let status: AppState.MenuBarStatus
    /// Draws a small dot on the icon when either update track is behind, so
    /// an update reads as a notification even with the window closed.
    var updateAvailable: Bool = false

    var body: some View {
        symbol
            .overlay(alignment: .topTrailing) {
                if updateAvailable {
                    Circle()
                        .fill(.primary)
                        .frame(width: 5, height: 5)
                        .offset(x: 3, y: -2)
                }
            }
    }

    @ViewBuilder private var symbol: some View {
        switch status {
        case .idle:          Image(systemName: "bolt")
        case .running:       Image(systemName: "circle.dotted")
        case .lastFailed:    Image(systemName: "exclamationmark.triangle")
        case .budgetSkipped: Image(systemName: "pause.circle")
        }
    }
}
