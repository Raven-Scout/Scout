import SwiftUI

/// The few Scout words the app can't avoid, with one plain definition each.
/// Shown in Help ▸ Scout Glossary and as tooltips (`.glossaryHelp(_:)`) where
/// the word appears. "Also called" lists the names older screens, the docs or
/// the engine use, so an owner who meets one of them can find it here.
nonisolated enum GlossaryTerm: String, CaseIterable, Identifiable, Sendable {
    case scoutFolder, knowledgeBase, schedule, run, session, engine
    case briefing, consolidation, dreaming, research, proposal, delegate
    /// What can happen to an action item (audit #51).
    case done, onHold, parked, archived

    var id: String { rawValue }

    var term: String {
        switch self {
        case .scoutFolder:   return "Scout folder"
        case .knowledgeBase: return "Knowledge base"
        case .schedule:      return "Schedule"
        case .run:           return "Run"
        case .session:       return "Session"
        case .engine:        return "Scout engine"
        case .briefing:      return "Briefing"
        case .consolidation: return "Consolidation"
        case .dreaming:      return "Dreaming"
        case .research:      return "Research"
        case .proposal:      return "Proposal"
        case .delegate:      return "Agent copy"
        case .done:          return "Done"
        case .onHold:        return "On hold"
        case .parked:        return "Parked"
        case .archived:      return "Archived"
        }
    }

    var definition: String {
        switch self {
        case .scoutFolder:
            return "The folder on your Mac where Scout keeps everything: your action items, notes and settings. Scout works only inside it."
        case .knowledgeBase:
            return "The notes Scout keeps about the people, projects and topics in your work, inside the Scout folder. You can read and edit them on the Knowledge Base page."
        case .schedule:
            return "A time Scout runs by itself, such as the morning briefing at 8:00 on weekdays. Each schedule has a name, a time, the days it runs and the kind of work it does."
        case .run:
            return "One time Scout does its work, either because a schedule's time came or because you chose Run now."
        case .session:
            return "A conversation with Claude Code on this Mac. The Sessions page lists them so you can pick up where you left off."
        case .engine:
            return "The part of Scout that works in the background: it runs the schedules and keeps the Scout folder up to date. This app shows what it does and lets you change its settings."
        case .briefing:
            return "The morning run that reads what came in overnight and writes today's action items."
        case .consolidation:
            return "A shorter run during the day that catches up on what changed since the last run."
        case .dreaming:
            return "A quieter run, every evening and on weekend mornings, that tidies the knowledge base, makes the changes you approved on the Proposals page, and may suggest new ones."
        case .research:
            return "A run that looks outward, on the web and in connected tools, for news about the topics you asked Scout to follow."
        case .proposal:
            return "A change Scout suggests to its own instructions. You approve or decline it on the Proposals page."
        case .delegate:
            return "A copy of part of the Scout folder that another agent may read, with private files left out."
        case .done:
            return "An action item you finished. It stays on the day's page, under Recently Completed, for about a week, so you can still read it and its comments."
        case .onHold:
            return "An action item that isn't asking for anything right now: it is snoozed until a later day, or it sits in Parked. A snoozed item comes back by itself on its date."
        case .parked:
            return "A section for action items set aside, by you or by Scout: a run parks a past-due task once it has gone weeks with no news. Parked items stay on the page but don't count as today's work; any new news on one puts it back in the list."
        case .archived:
            return "Older lists a Scout run folded away on the day's page, such as an earlier focus list. They sit behind a collapsed row for reference; nothing in them is lost."
        }
    }

    /// Other names for the same thing, so a search for any of them finds it.
    var alsoCalled: [String] {
        switch self {
        case .scoutFolder:   return ["vault", "Scout directory", "Scout instance"]
        case .knowledgeBase: return ["KB"]
        case .schedule:      return ["slot", "schedule slot"]
        case .run:           return ["scheduled run"]
        case .session:       return ["Claude Code session", "agent session"]
        case .engine:        return ["plugin", "scout-plugin", "scoutctl"]
        case .briefing:      return ["morning briefing", "weekend briefing"]
        case .consolidation: return []
        case .dreaming:      return []
        case .research:      return ["research topic"]
        case .proposal:      return ["skill change", "SKILL.md proposal"]
        case .delegate:      return ["delegate", "delegate mirror"]
        case .done:          return ["completed", "Recently Completed"]
        case .onHold:        return ["snoozed", "hold"]
        case .parked:        return ["🅿️ Parked"]
        case .archived:      return ["collapsed"]
        }
    }

    /// The tooltip text: the term and its definition.
    var tooltip: String { "\(term): \(definition)" }

    /// Terms whose name, other names or definition contain `query`.
    static func matching(_ query: String) -> [GlossaryTerm] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return allCases }
        return allCases.filter { t in
            ([t.term, t.definition] + t.alsoCalled).contains { $0.lowercased().contains(q) }
        }
    }
}

extension View {
    /// A tooltip that explains a Scout word, from the glossary.
    func glossaryHelp(_ term: GlossaryTerm) -> some View {
        help(term.tooltip)
    }

    /// `glossaryHelp(_:)` when there is a term; the view unchanged otherwise.
    @ViewBuilder
    func glossaryHelp(ifAny term: GlossaryTerm?) -> some View {
        if let term { help(term.tooltip) } else { self }
    }
}

/// Help ▸ Scout Glossary.
struct GlossaryView: View {
    @State private var query = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Scout Glossary")
                    .font(DS.font(.hero, .serif, weight: .semibold))
                    .foregroundStyle(DS.Ink.p1)
                Text("The words Scout uses, in plain language.")
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Ink.p2)
                TextField("Search", text: $query)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Search the glossary")
                    .padding(.top, 4)
            }
            .padding(20)
            Divider()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    let terms = GlossaryTerm.matching(query)
                    if terms.isEmpty {
                        Text("No term matches “\(query)”.")
                            .font(DS.font(.label))
                            .foregroundStyle(DS.Ink.p2)
                    }
                    ForEach(terms) { t in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(t.term)
                                .font(DS.font(.body, weight: .semibold))
                                .foregroundStyle(DS.Ink.p1)
                            Text(t.definition)
                                .font(DS.font(.label))
                                .foregroundStyle(DS.Ink.p1)
                                .fixedSize(horizontal: false, vertical: true)
                            if !t.alsoCalled.isEmpty {
                                Text("Also called: \(t.alsoCalled.joined(separator: ", "))")
                                    .font(DS.font(.caption))
                                    .foregroundStyle(DS.Ink.p2)
                            }
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(minWidth: 420, idealWidth: 480, minHeight: 420, idealHeight: 560)
        .background(DS.Paper.base)
    }
}

/// The Help-menu item that opens the glossary window.
struct GlossaryMenuItem: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("Scout Glossary") { openWindow(id: GlossaryView.windowID) }
    }
}

extension GlossaryView {
    static let windowID = "glossary"
}
