import SwiftUI

/// A titled column under the board: Inbox, Loose ends or Done.
struct SideListView<Header: View, Row: View>: View {
    let isEmpty: Bool
    @ViewBuilder let header: Header
    @ViewBuilder let rows: Row

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
                .font(DS.sans(12.5, weight: .medium))
                .foregroundStyle(DS.Ink.p3)
            if isEmpty {
                Text("–").foregroundStyle(DS.Ink.p3)
            } else {
                rows
            }
        }
        .frame(minWidth: 240, maxWidth: .infinity, alignment: .topLeading)
    }
}
