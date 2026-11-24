import SwiftUI

/// "What Scout is working on": the week's digest, pinned at the top of the Changes tab (spec §10).
///
/// The engine assembles it deterministically (no model call) and has already glossed every
/// identifier, so this view only lays the sections out. A change shows its one-sentence summary,
/// never its `chg-…` id; empty sections are left out.
struct DigestCard: View {
    let digest: ChangesDigest

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("What Scout is working on")
                    .font(DS.font(.body, .serif, weight: .semibold))
                    .foregroundStyle(DS.Ink.p1)
                if let week = Self.weekLabel(digest.weekOf) {
                    Text(week)
                        .font(DS.font(.caption))
                        .foregroundStyle(DS.Ink.p3)
                }
            }
            engagement
            if Self.isQuiet(digest) {
                Text("Nothing to report yet this week.")
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Ink.p2)
            }
            if !digest.testing.isEmpty {
                section("Testing now (\(digest.testing.count))") {
                    ForEach(digest.testing) { item in
                        row(item.summary, trailing: Self.daysLeft(item.daysLeft))
                    }
                }
            }
            if !digest.shipped.isEmpty {
                section("Just shipped (\(digest.shipped.count))") {
                    ForEach(digest.shipped) { item in row(item.summary) }
                }
            }
            if !digest.learned.isEmpty {
                section("What it learned") {
                    ForEach(digest.learned) { item in learnedRow(item) }
                }
            }
            if !digest.upNext.isEmpty {
                section("Up next") {
                    ForEach(digest.upNext) { item in row(item.title) }
                }
            }
            if digest.needsOK > 0 {
                Text(Self.needsOKLine(digest.needsOK))
                    .font(DS.font(.label, weight: .semibold))
                    .foregroundStyle(DS.Attention.you)
            }
        }
        .editorialCard()
        .accessibilityElement(children: .contain)
        .accessibilityLabel("What Scout is working on")
    }

    // MARK: - Pieces

    private var engagement: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(Self.engagementLine(digest.engagement, noise: digest.noise))
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p1)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                let spark = Self.sparkline(digest.days.map(\.engagement))
                if !spark.isEmpty {
                    Text(spark)
                        .font(DS.font(.label, .mono))
                        .foregroundStyle(DS.Ink.p2)
                        .accessibilityLabel("Engagement by day, \(digest.days.count) days")
                }
                Text("feedback \(digest.engagement.feedback) · sessions that used Scout \(digest.engagement.reliance)")
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p3)
            }
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(DS.font(.label, weight: .semibold))
                .foregroundStyle(DS.Ink.p1)
            content()
        }
    }

    private func row(_ text: String, trailing: String? = nil) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text("•").foregroundStyle(DS.Ink.p3).accessibilityHidden(true)
            Text(text)
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p1)
                .fixedSize(horizontal: false, vertical: true)
            if let trailing {
                Text("— \(trailing)")
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Ink.p3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func learnedRow(_ item: DigestLearned) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            DSPill(Self.verdictWords(item.verdict), tone: Self.verdictTone(item.verdict))
            Text(item.summary)
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p1)
                .fixedSize(horizontal: false, vertical: true)
            if item.suspect {
                DSPill("Suspect", tone: .warn, symbol: "exclamationmark.triangle")
            }
        }
    }

    // MARK: - Words (pure, so they are unit-tested)

    /// The card as plain lines, in reading order: what a screen reader and the tests see.
    nonisolated static func lines(for digest: ChangesDigest) -> [String] {
        var out = [engagementLine(digest.engagement, noise: digest.noise)]
        out += digest.testing.map { "\($0.summary) — \(daysLeft($0.daysLeft))" }
        out += digest.shipped.map(\.summary)
        out += digest.learned.map { "\(verdictWords($0.verdict)): \($0.summary)" + ($0.suspect ? " (suspect)" : "") }
        out += digest.upNext.map(\.title)
        if digest.needsOK > 0 { out.append(needsOKLine(digest.needsOK)) }
        return out
    }

    /// "Your engagement: 41 this week (up 12% vs last), noise 6 (down 2)".
    nonisolated static func engagementLine(_ engagement: DigestEngagement, noise: DigestNoise) -> String {
        let versus: String
        if let pct = engagement.changePct {
            let rounded = Int(pct.rounded())
            if rounded > 0 { versus = "up \(rounded)% vs last" }
            else if rounded < 0 { versus = "down \(-rounded)% vs last" }
            else { versus = "the same as last week" }
        } else {
            versus = "nothing from last week to compare"
        }
        let delta = noise.thisWeek - noise.lastWeek
        let noiseMove = delta > 0 ? "up \(delta)" : delta < 0 ? "down \(-delta)" : "no change"
        return "Your engagement: \(engagement.thisWeek) this week (\(versus)), noise \(noise.thisWeek) (\(noiseMove))"
    }

    nonisolated static func needsOKLine(_ count: Int) -> String {
        "Needs your OK (\(count)), shown below"
    }

    nonisolated static func daysLeft(_ days: Int) -> String {
        "\(days) day\(days == 1 ? "" : "s") left"
    }

    /// One block per day, scaled to the busiest day.
    nonisolated static func sparkline(_ values: [Int]) -> String {
        let blocks = Array("▁▂▃▄▅▆▇█")
        guard let top = values.max(), top > 0 else { return String(repeating: blocks[0], count: values.count) }
        return String(values.map { blocks[Int((Double(max($0, 0)) / Double(top) * 7).rounded())] })
    }

    /// "Week of Nov 16" from the Monday's `YYYY-MM-DD`; nil when the engine sent none.
    nonisolated static func weekLabel(_ weekOf: String) -> String? {
        guard !weekOf.isEmpty else { return nil }
        let parser = DateFormatter()
        parser.locale = Locale(identifier: "en_US_POSIX")
        parser.dateFormat = "yyyy-MM-dd"
        guard let date = parser.date(from: weekOf) else { return "Week of \(weekOf)" }
        return "Week of " + date.formatted(.dateTime.month(.abbreviated).day())
    }

    nonisolated static func verdictWords(_ verdict: String?) -> String {
        switch verdict {
        case "helped": return "Helped"
        case "hurt": return "Hurt"
        case "no clear effect": return "No clear effect"
        case "rolled back": return "Rolled back"
        case "can't tell": return "Can't tell"
        default: return "Not decided"
        }
    }

    nonisolated static func verdictTone(_ verdict: String?) -> DS.PillTone {
        switch verdict {
        case "helped": return .ok
        case "hurt", "rolled back": return .err
        default: return .neutral
        }
    }

    nonisolated static func isQuiet(_ digest: ChangesDigest) -> Bool {
        digest.testing.isEmpty && digest.shipped.isEmpty && digest.learned.isEmpty
            && digest.upNext.isEmpty && digest.needsOK == 0
    }
}
