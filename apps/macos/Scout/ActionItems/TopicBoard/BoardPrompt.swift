import Foundation

/// Ask-Claude prompts for the topic board: the item's own context plus where
/// it sits on the board — its topic, its state and what it waits on.
enum BoardPrompt {
    static func step(_ item: BoardItem, topic: String?) -> String {
        var out = ClaudeLauncher.prompt(for: item.task)
        out += "\n\nOn the board:\n" + boardLines(item, topic: topic).joined(separator: "\n")
        return out
    }

    static func topic(_ topic: BoardTopic) -> String {
        var out = "Help me make progress on this workstream: \(topic.title)"
        if let summary = topic.meta?.summary { out += "\n\n\(summary)" }
        if let session = topic.meta?.session { out += "\n\nRoot session: \(session)" }
        out += "\n\nSteps:\n" + flatten(topic.steps).map(stepLine).joined(separator: "\n")
        return out
    }

    static func boardLines(_ item: BoardItem, topic: String?) -> [String] {
        let fields = item.task.fields
        return [
            topic.map { "- Topic: \($0)" },
            "- State: \(item.state.label)",
            fields.depends.isEmpty ? nil : "- Waits on: " + fields.depends.map { "#\($0)" }.joined(separator: ", "),
            fields.session.map { "- Session: \($0)" },
        ].compactMap { $0 }
    }

    private static func stepLine(_ step: (item: BoardItem, depth: Int)) -> String {
        let task = step.item.task
        let tag = task.shortPrefix.map { " [#\($0)]" } ?? ""
        let body = task.summary.isEmpty ? "" : " — \(task.summary)"
        return String(repeating: "  ", count: step.depth) + "- \(step.item.state.label)\(tag): \(task.plainSubject)\(body)"
    }

    private static func flatten(_ steps: [BoardStep], depth: Int = 0) -> [(item: BoardItem, depth: Int)] {
        steps.flatMap { [($0.item, depth)] + flatten($0.nested, depth: depth + 1) }
    }
}
