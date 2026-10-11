import Foundation

/// Which board groups are open, stored as one newline-separated string so it
/// fits `@AppStorage`. Groups are collapsed unless listed.
nonisolated enum BoardExpansion {
    static func contains(_ key: String, in raw: String) -> Bool {
        keys(raw).contains(key)
    }

    static func setting(_ key: String, open: Bool, in raw: String) -> String {
        var keys = keys(raw).filter { $0 != key }
        if open { keys.append(key) }
        return keys.joined(separator: "\n")
    }

    private static func keys(_ raw: String) -> [String] {
        raw.split(separator: "\n").map(String.init)
    }
}
