import SwiftUI

/// On-battery warning banner (a `DSBanner`). Hidden when on AC or unknown — only the
/// `.onBattery` case warrants screen real estate. Dropped at the top of
/// `ControlCenterView` above `UpcomingStripView`.
struct PowerStateBanner: View {
    @ObservedObject var service: PowerStateService

    var body: some View {
        if case .onBattery = service.state {
            DSBanner(tone: .warning, title: "On battery",
                     message: "Runs may be missed if the lid closes. Plug in for guaranteed firing.",
                     symbol: "bolt.slash.fill")
                .padding(.bottom, 16)
        }
    }
}
