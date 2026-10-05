import SwiftUI

/// Yellow on-battery banner. Hidden when on AC or unknown — only the
/// `.onBattery` case warrants screen real estate. Dropped at the top of
/// `ControlCenterView` above `UpcomingStripView`.
struct PowerStateBanner: View {
    @ObservedObject var service: PowerStateService

    var body: some View {
        if case .onBattery = service.state {
            HStack {
                Image(systemName: "bolt.slash.fill")
                Text("On battery — runs may be missed if the lid closes. Plug in for guaranteed firing.")
                    .font(.callout)
            }
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.yellow.opacity(0.15))
            .foregroundColor(.yellow)
            .cornerRadius(6)
        }
    }
}
