import AppKit
import SwiftUI

/// A terminal or Ghostty launch that failed, as an alert shows it: the
/// error's text, plus the System Settings pane that fixes it when a missing
/// permission is the cause (UI audit #38).
nonisolated struct LaunchFailure: Equatable, Sendable {
    let message: String
    let settingsURL: URL?

    init(_ error: Error) {
        message = error.localizedDescription
        settingsURL = (error as? ClaudeLauncher.LaunchError)?.settingsURL
    }
}

extension View {
    /// "OK", plus "Open System Settings" when the failure names a pane.
    func launchFailureAlert(_ title: String, failure: Binding<LaunchFailure?>) -> some View {
        alert(title, isPresented: Binding(
            get: { failure.wrappedValue != nil },
            set: { if !$0 { failure.wrappedValue = nil } }
        ), presenting: failure.wrappedValue) { shown in
            if let url = shown.settingsURL {
                Button("Open System Settings") { NSWorkspace.shared.open(url) }
            }
            Button("OK", role: .cancel) {}
        } message: { shown in
            Text(shown.message)
        }
    }
}
