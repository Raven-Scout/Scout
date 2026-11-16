import SwiftUI

/// The Model section of Settings: which Claude model scheduled runs use when
/// their slot names none. A slot's own model is set in Schedules.
struct ModelSettingsSection: View {
    @EnvironmentObject var service: ModelSettingsService

    @State private var loadError: String?
    @State private var saveError: String?
    @State private var isSaving = false
    @State private var didLoad = false

    var body: some View {
        SettingsCard {
            if let settings = service.settings {
                SettingsField(
                    label: "Default model",
                    help: "The Claude model for every scheduled run whose slot doesn't name one. "
                        + "Give a single slot its own model in Schedules."
                ) {
                    picker(settings)
                }
                footer(settings)
            } else if let loadError {
                message(loadError, color: DS.Status.warn)
            } else {
                message("Reading model configuration…", color: DS.Ink.p4)
            }
        }
        .task { await load() }
    }

    private func picker(_ settings: ModelSettings) -> some View {
        let current = ModelChoice(model: settings.default.model)
        return EditorialSegmentedControl(
            selection: Binding(
                get: { current },
                set: { choice in
                    guard let model = choice.model, choice != current else { return }
                    Task { await save(model) }
                }
            ),
            options: ModelChoice.defaultOptions(current: current),
            minSegmentWidth: 70
        )
        .disabled(isSaving)
    }

    @ViewBuilder
    private func footer(_ settings: ModelSettings) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(
                "Cheaper models use less of your plan but can miss things Opus catches. "
                    + "Try one on a light slot first, such as a midday consolidation."
            )
            .font(DS.sans(11.5))
            .foregroundStyle(DS.Ink.p3)
            .fixedSize(horizontal: false, vertical: true)
            if settings.defaultIsBuiltIn {
                Text("No default set in scout-config.yaml, so runs use Opus.")
                    .font(DS.sans(11.5))
                    .foregroundStyle(DS.Ink.p4)
            }
            if let saveError {
                Text(saveError)
                    .font(DS.sans(11.5))
                    .foregroundStyle(DS.Status.warn)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 14)
    }

    private func message(_ text: String, color: Color) -> some View {
        Text(text)
            .font(DS.sans(12.5))
            .foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.vertical, 14)
    }

    private func load() async {
        guard !didLoad else { return }
        didLoad = true
        do {
            try await service.load()
            loadError = nil
        } catch {
            loadError = "Could not read the model configuration. This engine may predate per-slot "
                + "models; update Scout to choose them. (\(error.localizedDescription))"
        }
    }

    private func save(_ model: String) async {
        isSaving = true
        defer { isSaving = false }
        do {
            try await service.setDefault(model)
            saveError = nil
        } catch {
            saveError = error.localizedDescription
        }
    }
}
