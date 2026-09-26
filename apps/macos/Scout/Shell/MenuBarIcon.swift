import SwiftUI

struct MenuBarIcon: View {
    let status: AppState.MenuBarStatus

    var body: some View {
        switch status {
        case .idle:          Image(systemName: "bolt")
        case .running:       Image(systemName: "circle.dotted")
        case .lastFailed:    Image(systemName: "exclamationmark.triangle")
        case .budgetSkipped: Image(systemName: "pause.circle")
        }
    }
}
