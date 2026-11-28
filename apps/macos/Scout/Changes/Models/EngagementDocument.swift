import Foundation

/// `scoutctl engagement --json`: the owner's engagement and noise counts per day.
///
/// A contract: `ScoutTests/Fixtures/engagement.json` is a byte copy of the engine's
/// `plugin/engine/tests/fixtures/contract/engagement.json`. A bad day costs that
/// day only and is counted in `skippedRows` (computed here, not read from the JSON).
nonisolated struct EngagementDocument: Equatable, Sendable, Decodable {
    static let supportedSchemaVersion = 1

    let days: [EngagementDay]
    /// `feedback`, `noise` and `reliance` totals over the window.
    let totals: [String: Int]
    /// Owner DMs sent and still unanswered (`pending.dm_sent`).
    let pendingDMs: Int
    let skippedRows: Int

    private enum CodingKeys: String, CodingKey { case days, totals, pending }
    private struct Pending: Decodable { let dm_sent: Int? }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let rows: (rows: [EngagementDay], skipped: Int) = try ChangesDecoding.lossyRows(c, .days)
        days = rows.rows
        skippedRows = rows.skipped
        totals = try c.decodeIfPresent([String: Int].self, forKey: .totals) ?? [:]
        pendingDMs = (try c.decodeIfPresent(Pending.self, forKey: .pending))?.dm_sent ?? 0
    }

    static func parse(_ data: Data) -> Result<EngagementDocument, ChangesParseError> {
        ChangesDecoding.parse(data, as: EngagementDocument.self, supportedSchemaVersion: supportedSchemaVersion)
    }
}

nonisolated struct EngagementDay: Equatable, Sendable, Decodable {
    /// `YYYY-MM-DD`.
    let date: String
    let feedback: Int
    let reliance: Int
    let noise: Int

    private enum CodingKeys: String, CodingKey { case date, feedback, reliance, noise }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decode(String.self, forKey: .date)
        feedback = try c.decodeIfPresent(Int.self, forKey: .feedback) ?? 0
        reliance = try c.decodeIfPresent(Int.self, forKey: .reliance) ?? 0
        noise = try c.decodeIfPresent(Int.self, forKey: .noise) ?? 0
    }
}
