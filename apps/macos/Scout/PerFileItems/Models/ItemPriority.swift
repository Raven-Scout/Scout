// Scout/PerFileItems/Models/ItemPriority.swift
import Foundation

/// Priority of a per-file item. Wishlist uses high/medium/low; Research adds urgent.
nonisolated enum ItemPriority: String, Equatable, Sendable, Comparable, CaseIterable {
    case urgent, high, medium, low

    static func parse(_ raw: String) -> ItemPriority {
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "urgent": return .urgent
        case "high": return .high
        case "low": return .low
        default: return .medium   // "medium", missing, or unrecognized
        }
    }

    private var rank: Int {
        switch self { case .urgent: return 0; case .high: return 1; case .medium: return 2; case .low: return 3 }
    }
    static func < (lhs: ItemPriority, rhs: ItemPriority) -> Bool { lhs.rank < rhs.rank }

    var displayName: String { rawValue.capitalized }
}
