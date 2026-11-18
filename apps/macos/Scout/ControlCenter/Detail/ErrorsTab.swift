import SwiftUI

struct ErrorsTab: View {
    let errors: [DetectedError]

    var body: some View {
        if errors.isEmpty {
            Text("No errors detected.")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            List(Array(errors.enumerated()), id: \.offset) { _, err in
                VStack(alignment: .leading) {
                    Text("Line \(err.line) · \(err.pattern)")
                        .font(DS.sans(10))
                        .foregroundStyle(.secondary)
                    Text(err.snippet)
                        .font(DS.mono(13))
                        .lineLimit(2)
                }
                .padding(.vertical, 2)
            }
        }
    }
}
