import Foundation

/// Board state a task can declare with a `- State:` sub-bullet. Mirrors
/// `TASK_STATES` in `plugin/engine/scout/action_items/task_fields.py`.
nonisolated enum TaskState: String, CaseIterable, Sendable {
    case mine, doing, waiting, blocked, hold
}

/// Optional per-task board fields carried as machine sub-bullets under a task:
///
///     - [ ] [#REPLYX] **Reply to Alex about her purchase question**
///       - Topic: #STRM
///       - Parent: #RTALL
///       - State: mine
///       - Action: Reply
///       - Depends: #XREF, #QBLK
///       - Session: https://claude.ai/code/session_0123
///       - Priority: 2
///       - Cycle: current
///
/// The keys are reserved: a sub-bullet using one is metadata, never a comment,
/// even when its value is invalid (the value is then ignored). Must stay in
/// step with the engine's `task_fields.py`, which skips the same keys when it
/// indexes comments for `delete-comment` / `edit-comment --index`.
nonisolated struct TaskFields: Equatable, Hashable, Sendable {
    var topic: String?
    /// The item this one is a separate step of; the board nests it there.
    var parent: String?
    var state: TaskState?
    /// 1–3 word imperative for a `mine` item, e.g. "Merge", "Review + decide".
    var action: String?
    var depends: [String]
    var session: String?
    var priority: Int?
    var cycle: String?

    static let empty = TaskFields(topic: nil, parent: nil, state: nil, action: nil, depends: [], session: nil, priority: nil, cycle: nil)
}

nonisolated enum TaskFieldsParser {
    static let keys: Set<String> = ["topic", "parent", "state", "action", "depends", "session", "priority", "cycle"]

    private static let lineRe = try! NSRegularExpression(pattern: #"^\s+-\s+([A-Za-z]+)\s*:\s*(.*?)\s*$"#)
    private static let dependsTokenRe = try! NSRegularExpression(
        pattern: #"^\[?#?((?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{2,8})\]?$"#
    )
    private static let dependsSeparators = CharacterSet(charactersIn: ",·").union(.whitespaces)

    /// `(key, value)` when `line` is an indented reserved-key sub-bullet.
    static func match(_ line: String) -> (key: String, value: String)? {
        let ns = line as NSString
        guard let m = lineRe.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) else { return nil }
        let key = ns.substring(with: m.range(at: 1)).lowercased()
        guard keys.contains(key) else { return nil }
        return (key, ns.substring(with: m.range(at: 2)))
    }

    static func applying(key: String, value: String, to fields: TaskFields) -> TaskFields {
        var next = fields
        switch key {
        case "topic": next.topic = depends(value).first
        case "parent": next.parent = depends(value).first
        case "action": next.action = text(value)
        case "state": next.state = state(value)
        case "depends": next.depends = depends(value)
        case "session": next.session = text(value)
        case "priority": next.priority = priority(value)
        case "cycle": next.cycle = text(value)?.lowercased()
        default: break
        }
        return next
    }

    static func state(_ value: String) -> TaskState? {
        TaskState(rawValue: value.trimmingCharacters(in: .whitespaces).lowercased())
    }

    static func depends(_ value: String) -> [String] {
        value.components(separatedBy: dependsSeparators)
            .filter { !$0.isEmpty }
            .compactMap(dependsTag)
    }

    static func priority(_ value: String) -> Int? {
        let trimmed = value.trimmingCharacters(in: .whitespaces)
        guard trimmed.allSatisfy({ $0.isASCII && $0.isNumber }), let n = Int(trimmed), (0...4).contains(n) else { return nil }
        return n
    }

    private static func text(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func dependsTag(_ token: String) -> String? {
        let ns = token as NSString
        guard let m = dependsTokenRe.firstMatch(in: token, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range(at: 1)).uppercased()
    }
}
