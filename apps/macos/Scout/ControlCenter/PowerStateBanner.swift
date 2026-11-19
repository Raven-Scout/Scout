import SwiftUI

/// On-battery warning banner, in the status warning colour (audit #107: no
/// hard-coded yellow). Hidden when on AC or unknown — only the
/// `.onBattery` case warrants screen real estate. Dropped at the top of
/// `ControlCenterView` above `UpcomingStripView`.
struct PowerStateBanner: View {
    @ObservedObject var service: PowerStateService

    var body: some View {
        if case .onBattery = service.state {
            HStack {
                Image(systemName: "bolt.slash.fill")
                Text("On battery — runs may be missed if the lid closes. Plug in for guaranteed firing.")
                    .font(DS.font(.label))
            }
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.Status.warn.opacity(DS.Status.washOpacity))
            .foregroundStyle(DS.Status.warn)
            .cornerRadius(6)
        }
    }
}
