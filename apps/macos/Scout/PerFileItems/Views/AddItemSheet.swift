// Scout/PerFileItems/Views/AddItemSheet.swift
import SwiftUI

/// Modal sheet for adding a new per-file item (wishlist entry or research topic).
/// Form fields: Title (required — Add button disabled when blank), Priority picker
/// (from `config.priorities`, defaulting to `config.defaultPriority`), an optional
/// Source/Area field when `config.optionalField.label != nil`, and a multiline Notes
/// body. Submits via an async `onSubmit` closure; `onCancel` dismisses.
struct AddItemSheet: View {
    let config: PerFileTabConfig
    /// (title, priority, body, optionalFieldValue) — optional is source/area per config.
    let onSubmit: (String, ItemPriority, String, String?) async throws -> Void
    let onCancel: () -> Void

    @State private var title: String = ""
    @State private var priority: ItemPriority
    @State private var bodyText: String = ""
    @State private var optionalValue: String = ""
    @State private var submitting = false
    @State private var errorText: String?

    init(config: PerFileTabConfig,
         onSubmit: @escaping (String, ItemPriority, String, String?) async throws -> Void,
         onCancel: @escaping () -> Void) {
        self.config = config
        self.onSubmit = onSubmit
        self.onCancel = onCancel
        _priority = State(initialValue: config.defaultPriority)
    }

    private var canSubmit: Bool {
        !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !submitting
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add \(config.title) item")
                .font(DS.serif(18))
                .foregroundStyle(DS.Ink.p1)

            field("Title") {
                TextField("", text: $title)
                    .textFieldStyle(.roundedBorder)
            }

            field("Priority") {
                Picker("", selection: $priority) {
                    ForEach(config.priorities, id: \.self) { p in
                        Text(p.displayName).tag(p)
                    }
                }
                .labelsHidden()
                .pickerStyle(.segmented)
            }

            if let label = config.optionalField.label {
                field(label) {
                    TextField("", text: $optionalValue)
                        .textFieldStyle(.roundedBorder)
                }
            }

            field("Notes") {
                TextEditor(text: $bodyText)
                    .font(DS.serif(13.5))
                    .frame(minHeight: 120)
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(DS.Ink.p3.opacity(0.3))
                    )
            }

            if let errorText {
                Label(errorText, systemImage: "exclamationmark.triangle.fill")
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Status.err)
            }

            HStack {
                Spacer()
                Button("Cancel", action: onCancel)
                    .keyboardShortcut(.cancelAction)
                Button("Add") { submit() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!canSubmit)
            }
        }
        .padding(20)
        .frame(width: 460)
        .background(DS.Paper.base)
    }

    @ViewBuilder
    private func field<Content: View>(
        _ label: String,
        @ViewBuilder _ content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label.uppercased())
                .font(DS.mono(10))
                .tracking(0.6)
                .foregroundStyle(DS.Ink.p3)
            content()
        }
    }

    private func submit() {
        guard canSubmit else { return }
        errorText = nil
        submitting = true
        let optional = optionalValue.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            do {
                try await onSubmit(title, priority, bodyText, optional.isEmpty ? nil : optional)
            } catch {
                errorText = error.localizedDescription
                submitting = false
            }
        }
    }
}
