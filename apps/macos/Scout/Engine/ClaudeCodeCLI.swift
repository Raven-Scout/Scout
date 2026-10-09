import Foundation

/// The handful of Claude Code CLI invocations the installer drives, as tested
/// argv builders and decoders. Verified against Claude Code 2.1.259 (spec §12).
nonisolated enum ClaudeCodeCLI {
    static func marketplaceAdd(path: URL) -> [String] { ["plugin", "marketplace", "add", path.path] }
    static let marketplaceUpdate = ["plugin", "marketplace", "update", ClaudePluginsRegistry.scoutMarketplaceName]
    static let pluginInstall = ["plugin", "install", ClaudePluginsRegistry.scoutPluginID]
    static let pluginUpdate = ["plugin", "update", ClaudePluginsRegistry.scoutPluginID]
    static let authStatus = ["auth", "status", "--json"]
    static let version = ["--version"]

    /// Anthropic's documented native installer. Shown to the user and run in
    /// their own terminal — never inside the app (spec §4.3).
    static let installCommand = "curl -fsSL https://claude.ai/install.sh | bash"
    static func loginCommand(claude: URL) -> String { "\"\(claude.path)\" auth login" }

    nonisolated struct AuthStatus: Decodable, Equatable, Sendable {
        let loggedIn: Bool
        let authMethod: String?
        let subscriptionType: String?
    }

    static func parseAuthStatus(_ data: Data) -> AuthStatus? { try? JSONDecoder().decode(AuthStatus.self, from: data) }

    /// `claude --version` prints e.g. "2.1.259 (Claude Code)\n" — take the
    /// first whitespace-delimited token.
    static func parseVersion(_ data: Data) -> String? {
        guard let text = String(data: data, encoding: .utf8) else { return nil }
        let token = text.split(whereSeparator: \.isWhitespace).first.map(String.init)
        return token?.isEmpty == false ? token : nil
    }
}
