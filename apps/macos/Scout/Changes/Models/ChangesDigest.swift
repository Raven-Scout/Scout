import Foundation

/// `scoutctl changes digest --json` (engine `scout/changes/digest.py`): the weekly
/// "what changed, what it did" summary.
///
/// A contract: `ScoutTests/Fixtures/changes-digest.json` is a byte copy of the engine's
/// `plugin/engine/tests/fixtures/contract/changes-digest.json`. A bad row in any list
/// costs that row only and is counted in `skippedRows` (computed here).
nonisolated struct ChangesDigest: Equatable, Sendable, Decodable {
    static let supportedSchemaVersion = 1

    let generatedAt: Date
    /// `YYYY-MM-DD`, the Monday the week starts on.
    let weekOf: String
    let verified: Bool
    let days: [DigestDay]
    let engagement: DigestEngagement
    let noise: DigestNoise
    /// How many changes wait for the owner's decision.
    let needsOK: Int
    let shipped: [DigestChange]
    let testing: [DigestTesting]
    let learned: [DigestLearned]
    let upNext: [DigestUpNext]
    let skippedRows: Int

    private enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at", weekOf = "week_of", verified, days, engagement, noise
        case needsOK = "needs_ok", shipped, testing, learned, upNext = "up_next"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try ChangesDecoding.requiredTime(c, .generatedAt)
        weekOf = try c.decodeIfPresent(String.self, forKey: .weekOf) ?? ""
        verified = try c.decodeIfPresent(Bool.self, forKey: .verified) ?? false
        engagement = try c.decodeIfPresent(DigestEngagement.self, forKey: .engagement) ?? DigestEngagement()
        noise = try c.decodeIfPresent(DigestNoise.self, forKey: .noise) ?? DigestNoise()
        needsOK = try c.decodeIfPresent(Int.self, forKey: .needsOK) ?? 0
        let d: (rows: [DigestDay], skipped: Int) = try ChangesDecoding.lossyRows(c, .days)
        let s: (rows: [DigestChange], skipped: Int) = try ChangesDecoding.lossyRows(c, .shipped)
        let t: (rows: [DigestTesting], skipped: Int) = try ChangesDecoding.lossyRows(c, .testing)
        let l: (rows: [DigestLearned], skipped: Int) = try ChangesDecoding.lossyRows(c, .learned)
        let u: (rows: [DigestUpNext], skipped: Int) = try ChangesDecoding.lossyRows(c, .upNext)
        days = d.rows; shipped = s.rows; testing = t.rows; learned = l.rows; upNext = u.rows
        skippedRows = d.skipped + s.skipped + t.skipped + l.skipped + u.skipped
    }

    static func parse(_ data: Data) -> Result<ChangesDigest, ChangesParseError> {
        ChangesDecoding.parse(data, as: ChangesDigest.self, supportedSchemaVersion: supportedSchemaVersion)
    }
}

nonisolated struct DigestDay: Equatable, Sendable, Decodable {
    let date: String
    let engagement: Int
    let noise: Int

    private enum CodingKeys: String, CodingKey { case date, engagement, noise }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decode(String.self, forKey: .date)
        engagement = try c.decodeIfPresent(Int.self, forKey: .engagement) ?? 0
        noise = try c.decodeIfPresent(Int.self, forKey: .noise) ?? 0
    }
}

nonisolated struct DigestEngagement: Equatable, Sendable, Decodable {
    let feedback: Int
    let reliance: Int
    let thisWeek: Int
    let lastWeek: Int
    /// nil when last week had nothing to compare against.
    let changePct: Double?

    init(feedback: Int = 0, reliance: Int = 0, thisWeek: Int = 0, lastWeek: Int = 0, changePct: Double? = nil) {
        self.feedback = feedback; self.reliance = reliance
        self.thisWeek = thisWeek; self.lastWeek = lastWeek; self.changePct = changePct
    }

    private enum CodingKeys: String, CodingKey {
        case feedback, reliance, thisWeek = "this_week", lastWeek = "last_week", changePct = "change_pct"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        feedback = try c.decodeIfPresent(Int.self, forKey: .feedback) ?? 0
        reliance = try c.decodeIfPresent(Int.self, forKey: .reliance) ?? 0
        thisWeek = try c.decodeIfPresent(Int.self, forKey: .thisWeek) ?? 0
        lastWeek = try c.decodeIfPresent(Int.self, forKey: .lastWeek) ?? 0
        changePct = try c.decodeIfPresent(Double.self, forKey: .changePct)
    }
}

nonisolated struct DigestNoise: Equatable, Sendable, Decodable {
    let thisWeek: Int
    let lastWeek: Int

    init(thisWeek: Int = 0, lastWeek: Int = 0) { self.thisWeek = thisWeek; self.lastWeek = lastWeek }

    private enum CodingKeys: String, CodingKey { case thisWeek = "this_week", lastWeek = "last_week" }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        thisWeek = try c.decodeIfPresent(Int.self, forKey: .thisWeek) ?? 0
        lastWeek = try c.decodeIfPresent(Int.self, forKey: .lastWeek) ?? 0
    }
}

/// A change that shipped this week.
nonisolated struct DigestChange: Equatable, Sendable, Decodable, Identifiable {
    let id: String
    let summary: String
}

/// A change still being tested.
nonisolated struct DigestTesting: Equatable, Sendable, Decodable, Identifiable {
    let id: String
    let summary: String
    let daysLeft: Int

    private enum CodingKeys: String, CodingKey { case id, summary, daysLeft = "days_left" }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        summary = try c.decode(String.self, forKey: .summary)
        daysLeft = try c.decodeIfPresent(Int.self, forKey: .daysLeft) ?? 0
    }
}

/// A change whose test finished with a verdict.
nonisolated struct DigestLearned: Equatable, Sendable, Decodable, Identifiable {
    let id: String
    let summary: String
    let verdict: String?
    let suspect: Bool

    init(from decoder: Decoder) throws {
        enum Keys: String, CodingKey { case id, summary, verdict, suspect }
        let c = try decoder.container(keyedBy: Keys.self)
        id = try c.decode(String.self, forKey: .id)
        summary = try c.decode(String.self, forKey: .summary)
        verdict = try c.decodeIfPresent(String.self, forKey: .verdict)
        suspect = try c.decodeIfPresent(Bool.self, forKey: .suspect) ?? false
    }
}

/// A wishlist item the engine expects to work on next.
nonisolated struct DigestUpNext: Equatable, Sendable, Decodable, Identifiable {
    let slug: String
    let title: String
    var id: String { slug }
}
