import Foundation
import Testing
@testable import Scout

/// Covers note classification for the local-graph colouring — the ontology
/// `type:` override, the whole-path-component fallback, and the label/colour
/// each group renders with.
@Suite("KBEntityGroup — classification")
struct KBEntityGroupClassificationTests {

    // MARK: - frontmatter type wins

    @Test("an ontology type: overrides whatever the path says")
    func of_frontmatterTypeWins() {
        #expect(KBEntityGroup.of("projects/anything.md", type: "person") == .people)
        #expect(KBEntityGroup.of("people/anything.md", type: "project") == .projects)
        #expect(KBEntityGroup.of("channels/anything.md", type: "task") == .issues)
    }

    @Test("an unrecognised type: falls through to the path rules")
    func of_unknownTypeFallsThroughToPath() {
        #expect(KBEntityGroup.of("people/alex.md", type: "sasquatch") == .people)
        #expect(KBEntityGroup.of("misc/note.md", type: "sasquatch") == .other)
        #expect(KBEntityGroup.of("people/alex.md", type: nil) == .people)
    }

    // MARK: - path-directory classification

    @Test("a directory component classifies the note")
    func of_directoryComponents() {
        #expect(KBEntityGroup.of("people/alex.md") == .people)
        #expect(KBEntityGroup.of("projects/the-demo.md") == .projects)
        #expect(KBEntityGroup.of("issues/PROJ-1234.md") == .issues)
        #expect(KBEntityGroup.of("channels/general.md") == .channels)
        #expect(KBEntityGroup.of("ontology/types.md") == .ontology)
        #expect(KBEntityGroup.of("research-queue/topic.md") == .research)
        #expect(KBEntityGroup.of("review-queue/topic.md") == .research)
    }

    @Test("a nested directory component still classifies")
    func of_nestedDirectoryComponents() {
        #expect(KBEntityGroup.of("knowledge-base/people/priya.md") == .people)
        #expect(KBEntityGroup.of("a/b/c/issues/OPS-1.md") == .issues)
    }

    @Test("classification is case-insensitive")
    func of_isCaseInsensitive() {
        #expect(KBEntityGroup.of("People/Alex.md") == .people)
        #expect(KBEntityGroup.of("PROJECTS/Demo.MD") == .projects)
    }

    // MARK: - hub notes (stem, not directory)

    @Test("a hub note named for the group classifies by its stem")
    func of_hubNotesByStem() {
        #expect(KBEntityGroup.of("people.md") == .people)
        #expect(KBEntityGroup.of("projects.md") == .projects)
        #expect(KBEntityGroup.of("issues.md") == .issues)
        #expect(KBEntityGroup.of("channels.md") == .channels)
        #expect(KBEntityGroup.of("research-queue.md") == .research)
        #expect(KBEntityGroup.of("review-queue.md") == .research)
    }

    @Test("ontology is a directory-only rule — an ontology.md stem is Other")
    func of_ontologyHasNoStemRule() {
        #expect(KBEntityGroup.of("ontology.md") == .other)
    }

    // MARK: - the substring trap

    @Test("matching is on whole components, so tissue-sampling is not an issue")
    func of_matchesWholeComponentsNotSubstrings() {
        #expect(KBEntityGroup.of("tissue-sampling.md") == .other)
        #expect(KBEntityGroup.of("notes/peopled-areas.md") == .other)
        #expect(KBEntityGroup.of("my-projects-list.md") == .other)
        #expect(KBEntityGroup.of("subissues/x.md") == .other)
    }

    @Test("anything unclassified lands in Other")
    func of_defaultsToOther() {
        #expect(KBEntityGroup.of("") == .other)
        #expect(KBEntityGroup.of("random.md") == .other)
        #expect(KBEntityGroup.of("a/b/c.md") == .other)
    }

    // MARK: - presentation

    @Test("every group has a distinct non-empty label")
    func label_isUniquePerGroup() {
        let labels = KBEntityGroup.allCases.map(\.label)
        #expect(labels == ["People", "Projects", "Issues", "Channels",
                           "Ontology", "Research", "Other"])
        #expect(Set(labels).count == KBEntityGroup.allCases.count)
        #expect(labels.allSatisfy { !$0.isEmpty })
    }

    @Test("every group resolves a colour from the design system")
    func color_resolvesForEveryGroup() {
        #expect(KBEntityGroup.people.color == DS.Priority.personal)
        #expect(KBEntityGroup.projects.color == DS.SlotType.consolidation)
        #expect(KBEntityGroup.issues.color == DS.Priority.urgent)
        #expect(KBEntityGroup.channels.color == DS.Accent.fill)
        #expect(KBEntityGroup.ontology.color == DS.SlotType.dreaming)
        #expect(KBEntityGroup.research.color == DS.SlotType.research)
        #expect(KBEntityGroup.other.color == DS.Ink.p3)
    }

    @Test("raw values are stable — they key persisted graph state")
    func rawValues_areStable() {
        #expect(KBEntityGroup.allCases.map(\.rawValue)
                == ["people", "projects", "issues", "channels",
                    "ontology", "research", "other"])
        #expect(KBEntityGroup(rawValue: "people") == .people)
        #expect(KBEntityGroup(rawValue: "nonsense") == nil)
    }
}

/// The small value types the graph and right-hand panel are built from.
@Suite("KB graph value types")
struct KBGraphValueTypeTests {

    @Test("a link is identified by its original target text")
    func kbLink_identity() {
        let resolved = KBLink(target: "Alex", resolved: "people/alex.md")
        let dangling = KBLink(target: "Ghost", resolved: nil)
        #expect(resolved.id == "Alex")
        #expect(dangling.id == "Ghost")
        #expect(dangling.resolved == nil)
        #expect(resolved != dangling)
    }

    @Test("backlinks and search hits are identified by path")
    func kbBacklinkAndHit_identity() {
        let back = KBBacklink(path: "people/alex.md", name: "Alex", excerpt: "…the demo…")
        #expect(back.id == "people/alex.md")

        let hit = KBSearchHit(path: "projects/demo.md", name: "Demo", snippet: "…tracing…")
        #expect(hit.id == "projects/demo.md")
        #expect(hit == KBSearchHit(path: "projects/demo.md", name: "Demo", snippet: "…tracing…"))
    }

    @Test("a graph node carries its group, degree, and centre flag")
    func kbGraphNode_fields() {
        let node = KBGraphNode(id: "people/alex.md", label: "Alex",
                               group: .people, degree: 3, isCenter: true)
        #expect(node.id == "people/alex.md")
        #expect(node.group == .people)
        #expect(node.degree == 3)
        #expect(node.isCenter)
    }

    @Test("edges are hashable so they de-duplicate in a set")
    func kbGraphEdge_isHashable() {
        let a = KBGraphEdge(from: "x", to: "y")
        let b = KBGraphEdge(from: "x", to: "y")
        let c = KBGraphEdge(from: "y", to: "x")
        #expect(Set([a, b, c]).count == 2)      // a == b; c is the reverse edge
    }

    @Test("the empty graph and empty index have no contents")
    func emptyStaticValues() {
        #expect(KBGraph.empty.nodes.isEmpty)
        #expect(KBGraph.empty.edges.isEmpty)
        #expect(KBGraph.empty == KBGraph(nodes: [], edges: []))

        #expect(KBIndex.empty.stemToPath.isEmpty)
        #expect(KBIndex.empty.outByFile.isEmpty)
        #expect(KBIndex.empty.textByFile.isEmpty)
        #expect(KBIndex.empty.typeByFile.isEmpty)
    }
}
