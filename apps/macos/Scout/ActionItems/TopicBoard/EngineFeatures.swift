import Foundation

/// The `features` an engine advertises in `scoutctl manifest show`
/// (engine `scout/manifest.py`), as the set of names that are on.
nonisolated enum EngineFeatures {
    private struct Manifest: Decodable {
        let features: [String: Bool]
    }

    static func decode(_ data: Data) -> Set<String> {
        guard let manifest = try? JSONDecoder().decode(Manifest.self, from: data) else { return [] }
        return Set(manifest.features.filter(\.value).keys)
    }
}
