import SwiftUI

struct StateDot: View {
    let state: BoardState
    var size: CGFloat = 8

    var body: some View {
        Group {
            if state.isRing {
                Circle().strokeBorder(state.color, lineWidth: 1.5)
            } else {
                Circle().fill(state.color)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
