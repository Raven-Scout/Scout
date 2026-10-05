import Foundation

struct UsageEntry: Codable, Equatable, Sendable {
    let ts: Date
    let tsET: String
    let type: String
    let budgetCap: Decimal?
    let budgetSpent: Decimal?
    let exitCode: Int?
    let source: String?     // "session" | "runner" | nil (legacy)

    enum CodingKeys: String, CodingKey {
        case ts
        case tsET = "ts_et"
        case type
        case budgetCap = "budget_cap"
        case budgetSpent = "budget_spent"
        case exitCode = "exit_code"
        case source
    }
}
