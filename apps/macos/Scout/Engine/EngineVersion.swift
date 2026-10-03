import Foundation

/// `major.minor.patch[-pre]`; pre-release sorts before the release. #74's
/// `SemVer` covers the same ground — dedupe onto one type when both exist.
nonisolated struct EngineVersion: Equatable, Comparable, Sendable, CustomStringConvertible {
    let major: Int, minor: Int, patch: Int
    let preRelease: String?

    init?(_ text: String) {
        let trimmed = text.hasPrefix("v") ? String(text.dropFirst()) : text
        let core = trimmed.split(separator: "-", maxSplits: 1, omittingEmptySubsequences: false)
        let parts = core[0].split(separator: ".").map { Int($0) }
        guard parts.count == 3, let a = parts[0], let b = parts[1], let c = parts[2] else { return nil }
        major = a; minor = b; patch = c
        preRelease = core.count == 2 && !core[1].isEmpty ? String(core[1]) : nil
    }

    static func < (l: EngineVersion, r: EngineVersion) -> Bool {
        if (l.major, l.minor, l.patch) != (r.major, r.minor, r.patch) { return (l.major, l.minor, l.patch) < (r.major, r.minor, r.patch) }
        switch (l.preRelease, r.preRelease) {
        case (nil, nil): return false
        case (.some, nil): return true
        case (nil, .some): return false
        case (.some(let a), .some(let b)): return a < b
        }
    }
    var description: String { "\(major).\(minor).\(patch)" + (preRelease.map { "-\($0)" } ?? "") }
}
