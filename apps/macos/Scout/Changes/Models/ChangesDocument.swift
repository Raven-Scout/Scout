import Foundation

/// Why a changes-map document could not be read.
nonisolated enum ChangesParseError: Error, Equatable {
    /// Not the document (an older engine without the command, or an unrelated line).
    case notADocument
    /// A newer engine whose document shape this build doesn't know.
    case unsupportedSchema(Int)
    /// The engine said why (`{"error": …}`), e.g. a missing vault.
    case engine(String)
}

/// Shared parsing for the three changes-map documents, modelled on `RunLedger.parse`.
nonisolated enum ChangesDecoding {
    private struct Probe: Decodable { let schema_version: Int?; let error: String? }

    static func parse<T: Decodable>(_ data: Data, as type: T.Type, supportedSchemaVersion: Int) -> Result<T, ChangesParseError> {
        guard let probe = try? JSONDecoder().decode(Probe.self, from: data) else { return .failure(.notADocument) }
        if let error = probe.error { return .failure(.engine(error)) }
        guard let version = probe.schema_version else { return .failure(.notADocument) }
        guard version <= supportedSchemaVersion else { return .failure(.unsupportedSchema(version)) }
        guard let document = try? JSONDecoder().decode(T.self, from: data) else { return .failure(.notADocument) }
        return .success(document)
    }

    /// An ISO time as the engine writes it: `2026-11-16T12:00:00+00:00` or `…Z`.
    static func time(_ raw: String) -> Date? {
        if let date = RunLedgerTime.parse(raw) { return date }
        return try? Date(raw, strategy: Date.ISO8601FormatStyle(timeZoneSeparator: .colon))
    }

    static func requiredTime<K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K) throws -> Date {
        let raw = try c.decode(String.self, forKey: key)
        guard let date = time(raw) else {
            throw DecodingError.dataCorruptedError(forKey: key, in: c, debugDescription: "not an ISO time: \(raw)")
        }
        return date
    }

    /// Decodes a list of rows, dropping each row that doesn't decode; returns the rows and how many were dropped.
    static func lossyRows<T: Decodable & Sendable, K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K) throws -> (rows: [T], skipped: Int) {
        let lossy = try c.decodeIfPresent([ChangesLossy<T>].self, forKey: key) ?? []
        let rows = lossy.compactMap(\.value)
        return (rows, lossy.count - rows.count)
    }
}

/// Decodes `T`, or nil when that one element doesn't decode.
nonisolated struct ChangesLossy<T: Decodable & Sendable>: Decodable, Sendable {
    let value: T?
    init(from decoder: Decoder) throws { value = try? T(from: decoder) }
}

/// `scoutctl changes --json` (engine `scout/changes`): the change history, each
/// change with its test verdict, plus the owner's pending approvals.
///
/// A contract: `ScoutTests/Fixtures/changes.json` is a byte copy of the engine's
/// `plugin/engine/tests/fixtures/contract/changes.json` (`contract.yml` compares
/// them). Unknown keys are ignored and enum-like values stay `String`. A bad row
/// costs that row only and is counted in `skippedRows`.
nonisolated struct ChangesDocument: Equatable, Sendable, Decodable {
    static let supportedSchemaVersion = 1

    let generatedAt: Date
    let verified: Bool
    let calibrating: Bool
    let autoRollback: String
    let changes: [ChangeRow]
    let needsOK: [NeedsOKItem]
    /// Rows this build couldn't read, counted here (not read from the JSON).
    let skippedRows: Int

    private enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at", verified, calibrating, autoRollback = "auto_rollback"
        case changes, needsOK = "needs_ok"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try ChangesDecoding.requiredTime(c, .generatedAt)
        verified = try c.decodeIfPresent(Bool.self, forKey: .verified) ?? false
        calibrating = try c.decodeIfPresent(Bool.self, forKey: .calibrating) ?? false
        autoRollback = try c.decodeIfPresent(String.self, forKey: .autoRollback) ?? "off"
        let rows: (rows: [ChangeRow], skipped: Int) = try ChangesDecoding.lossyRows(c, .changes)
        let items: (rows: [NeedsOKItem], skipped: Int) = try ChangesDecoding.lossyRows(c, .needsOK)
        changes = rows.rows
        needsOK = items.rows
        skippedRows = rows.skipped + items.skipped
    }

    static func parse(_ data: Data) -> Result<ChangesDocument, ChangesParseError> {
        ChangesDecoding.parse(data, as: ChangesDocument.self, supportedSchemaVersion: supportedSchemaVersion)
    }
}

/// One change, as the engine's history shows it.
nonisolated struct ChangeRow: Equatable, Sendable, Decodable, Identifiable {
    let id: String
    let kind: String
    let summary: String
    let commit: String
    let ts: Date
    let why: ChangeWhy
    let files: [String]
    let targets: [String]
    let governance: Bool
    let backfilled: Bool
    let authorLane: String
    /// The change this one reverts, when it is a rollback.
    let reverts: String?
    let test: ChangeTest

    private enum CodingKeys: String, CodingKey {
        case id, kind, summary, commit, ts, why, files, targets, governance, backfilled
        case authorLane = "author_lane", reverts, test
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = try c.decode(String.self, forKey: .kind)
        summary = try c.decode(String.self, forKey: .summary)
        commit = try c.decode(String.self, forKey: .commit)
        ts = try ChangesDecoding.requiredTime(c, .ts)
        why = try c.decodeIfPresent(ChangeWhy.self, forKey: .why) ?? ChangeWhy()
        files = try c.decodeIfPresent([String].self, forKey: .files) ?? []
        targets = try c.decodeIfPresent([String].self, forKey: .targets) ?? []
        governance = try c.decodeIfPresent(Bool.self, forKey: .governance) ?? false
        backfilled = try c.decodeIfPresent(Bool.self, forKey: .backfilled) ?? false
        authorLane = try c.decodeIfPresent(String.self, forKey: .authorLane) ?? ""
        reverts = try c.decodeIfPresent(String.self, forKey: .reverts)
        test = try c.decodeIfPresent(ChangeTest.self, forKey: .test) ?? ChangeTest()
    }
}

/// The reason a change happened. `gloss` is the plain-words line the tab shows.
nonisolated struct ChangeWhy: Equatable, Sendable, Decodable {
    let type: String
    let ref: String
    /// Empty when the engine had no plain wording (the fixture sends `null`).
    let gloss: String
    let inferred: Bool

    init(type: String = "unknown", ref: String = "", gloss: String = "", inferred: Bool = true) {
        self.type = type; self.ref = ref; self.gloss = gloss; self.inferred = inferred
    }

    private enum CodingKeys: String, CodingKey { case type, ref, gloss, inferred }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = try c.decodeIfPresent(String.self, forKey: .type) ?? "unknown"
        ref = try c.decodeIfPresent(String.self, forKey: .ref) ?? ""
        gloss = try c.decodeIfPresent(String.self, forKey: .gloss) ?? ""
        inferred = try c.decodeIfPresent(Bool.self, forKey: .inferred) ?? true
    }
}

/// How a change's test stands.
nonisolated struct ChangeTest: Equatable, Sendable, Decodable {
    /// not-tested | testing | done; anything else is shown as not tested.
    let state: String
    /// helped | hurt | no clear effect | rolled back | can't tell; nil while untested.
    let verdict: String?
    let suspect: Bool
    let markedWrong: Bool
    let calibrating: Bool
    let engagementTargeted: Bool
    /// `YYYY-MM-DD`.
    let reviewOn: String?
    let rolledBackBy: String?
    let overlap: [String]
    let reasons: [String]
    let baseline: TestWindow?
    let after: TestWindow?
    let wouldHaveDone: [String: Bool]

    init(state: String = "not-tested", verdict: String? = nil, suspect: Bool = false, markedWrong: Bool = false,
         calibrating: Bool = false, engagementTargeted: Bool = false, reviewOn: String? = nil,
         rolledBackBy: String? = nil, overlap: [String] = [], reasons: [String] = [],
         baseline: TestWindow? = nil, after: TestWindow? = nil, wouldHaveDone: [String: Bool] = [:]) {
        self.state = state; self.verdict = verdict; self.suspect = suspect; self.markedWrong = markedWrong
        self.calibrating = calibrating; self.engagementTargeted = engagementTargeted; self.reviewOn = reviewOn
        self.rolledBackBy = rolledBackBy; self.overlap = overlap; self.reasons = reasons
        self.baseline = baseline; self.after = after; self.wouldHaveDone = wouldHaveDone
    }

    private enum CodingKeys: String, CodingKey {
        case state, verdict, suspect, markedWrong = "marked_wrong", calibrating
        case engagementTargeted = "engagement_targeted", reviewOn = "review_on", rolledBackBy = "rolled_back_by"
        case overlap, reasons, baseline, after, wouldHaveDone = "would_have_done"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? "not-tested"
        verdict = try c.decodeIfPresent(String.self, forKey: .verdict)
        suspect = try c.decodeIfPresent(Bool.self, forKey: .suspect) ?? false
        markedWrong = try c.decodeIfPresent(Bool.self, forKey: .markedWrong) ?? false
        calibrating = try c.decodeIfPresent(Bool.self, forKey: .calibrating) ?? false
        engagementTargeted = try c.decodeIfPresent(Bool.self, forKey: .engagementTargeted) ?? false
        reviewOn = try c.decodeIfPresent(String.self, forKey: .reviewOn)
        rolledBackBy = try c.decodeIfPresent(String.self, forKey: .rolledBackBy)
        overlap = try c.decodeIfPresent([String].self, forKey: .overlap) ?? []
        reasons = try c.decodeIfPresent([String].self, forKey: .reasons) ?? []
        baseline = try c.decodeIfPresent(TestWindow.self, forKey: .baseline)
        after = try c.decodeIfPresent(TestWindow.self, forKey: .after)
        wouldHaveDone = try c.decodeIfPresent([String: Bool].self, forKey: .wouldHaveDone) ?? [:]
    }
}

/// One measured window of a change's test (before or after it landed).
nonisolated struct TestWindow: Equatable, Sendable, Decodable {
    let days: Int
    let awayDays: Int
    let engagement: Double
    let noise: Double
    let recurrences: [String: Int]

    private enum CodingKeys: String, CodingKey { case days, awayDays = "away_days", engagement, noise, recurrences }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        days = try c.decode(Int.self, forKey: .days)
        awayDays = try c.decodeIfPresent(Int.self, forKey: .awayDays) ?? 0
        engagement = try c.decodeIfPresent(Double.self, forKey: .engagement) ?? 0
        noise = try c.decodeIfPresent(Double.self, forKey: .noise) ?? 0
        recurrences = try c.decodeIfPresent([String: Int].self, forKey: .recurrences) ?? [:]
    }
}

/// A change waiting for the owner's decision.
nonisolated struct NeedsOKItem: Equatable, Sendable, Decodable, Identifiable {
    let id: String
    let summary: String
    let why: String
    let path: String
    /// awaiting-ok | approved | …; kept as a `String`.
    let state: String
    /// The 64-hex hash an approval must quote.
    let diffHash: String
    let targets: [String]
    /// Whether applying it changes what Scout may do on its own. Missing means true, the engine's own
    /// default, so the sheet never calls a governance change ordinary by accident.
    let governance: Bool
    /// The engine's plain words for `why` (`pattern-288` → that pattern's title); nil when it has none.
    let whyGloss: String?
    /// One entry per target, nil where the engine has no plain words for it.
    let targetsGloss: [String?]
    /// The pending file's own explanation, the prose around its diff; nil when it has none.
    let context: String?

    private enum CodingKeys: String, CodingKey {
        case id, summary, why, path, state, diffHash = "diff_hash", targets, governance
        case whyGloss = "why_gloss", targetsGloss = "targets_gloss", context
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        summary = try c.decode(String.self, forKey: .summary)
        why = try c.decodeIfPresent(String.self, forKey: .why) ?? ""
        path = try c.decode(String.self, forKey: .path)
        state = try c.decode(String.self, forKey: .state)
        diffHash = try c.decode(String.self, forKey: .diffHash)
        targets = try c.decodeIfPresent([String].self, forKey: .targets) ?? []
        governance = try c.decodeIfPresent(Bool.self, forKey: .governance) ?? true
        whyGloss = try c.decodeIfPresent(String.self, forKey: .whyGloss)
        targetsGloss = (try? c.decodeIfPresent([String?].self, forKey: .targetsGloss)) ?? []
        context = try c.decodeIfPresent(String.self, forKey: .context)
    }

    /// Why it is waiting, in plain words: the engine's gloss, else the file's own `why` when that is prose.
    /// A bare reference (`pattern-288`, `owner-ask:…`) is never shown; nil then.
    var whyLine: String? {
        if let gloss = Self.nonEmpty(whyGloss) { return gloss }
        guard let raw = Self.nonEmpty(why), raw.contains(where: \.isWhitespace) else { return nil }
        return raw
    }

    /// What it changes, each target by its plain name: the engine's gloss, else the target itself unless it is a
    /// pattern reference, which reads "a recurring mistake".
    var targetNames: [String] {
        targets.enumerated().map { index, target in
            if index < targetsGloss.count, let gloss = Self.nonEmpty(targetsGloss[index]) { return gloss }
            return target.hasPrefix("pattern-") ? "a recurring mistake" : target
        }
    }

    /// The context paragraph to show, when there is one.
    var contextLine: String? { Self.nonEmpty(context) }

    private static func nonEmpty(_ text: String?) -> String? {
        guard let text = text?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        return text
    }
}
