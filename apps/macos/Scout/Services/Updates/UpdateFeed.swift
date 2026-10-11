import Foundation

/// Where the app looks for updates.
///
/// The production feed is baked into Info.plist (`SUFeedURL`). For end-to-end
/// testing of a release candidate against a pre-release appcast, the feed can
/// be overridden per process with the `SCOUT_APPCAST_URL` environment
/// variable:
///
///     open -a /path/to/Scout.app --env SCOUT_APPCAST_URL=https://…/appcast.xml
///
/// Only well-formed `https` URLs are honored; anything else falls back to
/// `SUFeedURL`. The override cannot weaken security: Sparkle still requires
/// the enclosure to carry a valid EdDSA signature (private key on the release
/// machine) and the downloaded app to be signed by the same Developer ID team.
enum UpdateFeed {
    static let overrideEnvironmentKey = "SCOUT_APPCAST_URL"

    nonisolated static func overrideURLString(environment: [String: String]) -> String? {
        guard let raw = environment[overrideEnvironmentKey]?
                .trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty,
              let url = URL(string: raw),
              url.scheme?.lowercased() == "https",
              let host = url.host, !host.isEmpty
        else { return nil }
        return raw
    }
}
