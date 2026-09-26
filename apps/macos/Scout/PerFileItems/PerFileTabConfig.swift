// Scout/PerFileItems/PerFileTabConfig.swift
import Foundation

/// Per-tab knobs that parameterize the shared per-file UI/writer.
struct PerFileTabConfig: Sendable, Equatable {
    enum OptionalField: Sendable, Equatable {
        case none
        case source(label: String)
        case area(label: String)
        var label: String? {
            switch self {
            case .none: return nil
            case .source(let l), .area(let l): return l
            }
        }
    }

    let title: String
    let priorities: [ItemPriority]
    let defaultPriority: ItemPriority
    let optionalField: OptionalField
    let addNoun: String                  // commit message noun, e.g. "wishlist item"
    let directoryDefaultRelative: String // relative to scoutDir
    let pathOverrideKey: String          // UserDefaults override key

    static let wishlist = PerFileTabConfig(
        title: "Wishlist",
        priorities: [.high, .medium, .low],
        defaultPriority: .medium,
        optionalField: .source(label: "Source"),
        addNoun: "wishlist item",
        directoryDefaultRelative: "docs/wishlist",
        pathOverrideKey: "wishlistPath"
    )

    static let research = PerFileTabConfig(
        title: "Research",
        priorities: [.urgent, .high, .medium, .low],
        defaultPriority: .medium,
        optionalField: .area(label: "Area"),
        addNoun: "research topic",
        directoryDefaultRelative: "knowledge-base/research-queue",
        pathOverrideKey: "researchQueuePath"
    )
}
