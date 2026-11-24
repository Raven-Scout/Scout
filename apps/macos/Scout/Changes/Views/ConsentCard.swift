import SwiftUI

/// The one-time question at the top of the Changes tab: may Scout count Claude sessions outside the vault
/// that used Scout? Shown while the owner's answer is "not asked". The answer lives in the owner store (read
/// and written through `scoutctl`), so a new install asks again and an upgrade that already has one doesn't.
struct ConsentCard: View {
    @ObservedObject var decisions: ChangesDecisions
    @State private var message: String?
    @State private var working = false

    static let title = "Count your Claude sessions outside the vault?"
    static let explanation = "Scout learns what's useful by noticing when you rely on it. With your OK it also notes sessions outside the vault that used Scout, only that one did, never what it read or where. You can change this in Settings."
    static let yesLabel = "Yes, count them"
    static let noLabel = "No"

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(Self.title)
                .dsFont(.body, .serif, weight: .semibold)
                .foregroundStyle(DS.Ink.p1)
            Text(Self.explanation)
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Button(Self.yesLabel) { send(true) }
                    .buttonStyle(.ds(.primary, size: .small))
                Button(Self.noLabel) { send(false) }
                    .buttonStyle(.ds(.secondary, size: .small))
            }
            .disabled(working)
            if let message {
                Text(message)
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Status.warn)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .editorialCard()
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Self.title)
    }

    private func send(_ allowed: Bool) {
        working = true
        Task {
            message = await Self.answer(allowed, decisions: decisions)
            working = false
        }
    }

    /// Sends the answer. Returns the words to show when the engine said no, nil when it took it.
    @discardableResult
    static func answer(_ allowed: Bool, decisions: ChangesDecisions) async -> String? {
        switch await decisions.perform(.consent(allowed)) {
        case .done: return nil
        case .refused(let why): return why
        case .invalid: return "Scout couldn't save that."
        }
    }
}

/// Settings ▸ Changes map: the owner's out-of-vault answer in plain words, and a button to change it.
/// Wrap it in a `SettingsCard`.
struct ChangesMapSettingsSection: View {
    @ObservedObject var decisions: ChangesDecisions
    @State private var message: String?

    static func answerWords(_ answer: ConsentAnswer) -> String {
        switch answer {
        case .yes: return "Counting: Scout notes Claude sessions outside the vault that used Scout, only that one did, never what they read or where."
        case .no: return "Not counting: Scout ignores Claude sessions outside the vault."
        case .notAsked: return "Not answered yet. Scout asks once, at the top of the Changes page."
        case .unknown: return "Scout couldn't read your answer just now."
        }
    }

    /// The answer a click would send, or nil when there is nothing to change to yet.
    static func change(from answer: ConsentAnswer) -> Bool? {
        switch answer {
        case .yes: return false
        case .no, .notAsked: return true
        case .unknown: return nil
        }
    }

    static func apply(_ allowed: Bool, decisions: ChangesDecisions) async {
        _ = await decisions.perform(.consent(allowed))
    }

    var body: some View {
        let next = Self.change(from: decisions.consent)
        SettingsRow(title: "Count sessions outside the vault", help: message ?? Self.answerWords(decisions.consent)) {
            if let next {
                Button(next ? "Start counting" : "Stop counting") {
                    Task { message = await ConsentCard.answer(next, decisions: decisions) }
                }
                .buttonStyle(.ds(.secondary, size: .small))
            }
        }
        .task { _ = await decisions.perform(.consentShow) }
    }
}
