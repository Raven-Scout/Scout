import Foundation

/// A single scheduled fire within a calendar-based plist trigger.
/// Weekday uses Calendar convention: 1=Sun, 2=Mon, 3=Tue, 4=Wed, 5=Thu,
/// 6=Fri, 7=Sat. nil means "every day" (no Weekday key in the plist).
/// PlistIO converts at the I/O boundary; launchd's raw 0/7 both map to 1.
struct CalendarFire: Identifiable, Equatable, Hashable, Sendable {
    let id: UUID
    var weekday: Int?
    var hour: Int
    var minute: Int

    init(id: UUID = UUID(), weekday: Int?, hour: Int, minute: Int) {
        self.id = id
        self.weekday = weekday
        self.hour = hour
        self.minute = minute
    }

    /// Equality ignoring `id` — used for diffing edits against originals.
    func semanticallyEquals(_ other: CalendarFire) -> Bool {
        weekday == other.weekday && hour == other.hour && minute == other.minute
    }
}

enum ScheduleTrigger: Equatable, Hashable, Sendable {
    case calendar([CalendarFire])
    case interval(seconds: Int)

    /// Equality that ignores `CalendarFire.id` so two triggers with the same
    /// fires (generated at different times) compare equal.
    func semanticallyEquals(_ other: ScheduleTrigger) -> Bool {
        switch (self, other) {
        case (.interval(let a), .interval(let b)):
            return a == b
        case (.calendar(let a), .calendar(let b)):
            guard a.count == b.count else { return false }
            return zip(a, b).allSatisfy { $0.semanticallyEquals($1) }
        default:
            return false
        }
    }
}

/// A tagged-union mirror of the plist value types we care about. Used to
/// round-trip `Schedule.unknownKeys` without destroying them on save.
indirect enum PlistValue: Equatable, Hashable, Sendable {
    case string(String)
    case int(Int)
    case bool(Bool)
    case date(Date)
    case data(Data)
    case array([PlistValue])
    case dict([String: PlistValue])

    func toObject() -> Any {
        switch self {
        case .string(let s): return s
        case .int(let i): return i
        case .bool(let b): return b
        case .date(let d): return d
        case .data(let d): return d
        case .array(let a): return a.map { $0.toObject() }
        case .dict(let d): return d.mapValues { $0.toObject() }
        }
    }

    static func from(object: Any) -> PlistValue {
        // NSNumber from PropertyListSerialization can be either Bool or numeric.
        // CFBooleanGetTypeID distinguishes Bool from Int.
        if let n = object as? NSNumber {
            if CFGetTypeID(n) == CFBooleanGetTypeID() {
                return .bool(n.boolValue)
            }
            return .int(n.intValue)
        }
        if let s = object as? String { return .string(s) }
        if let d = object as? Date { return .date(d) }
        if let d = object as? Data { return .data(d) }
        if let a = object as? [Any] { return .array(a.map { PlistValue.from(object: $0) }) }
        if let d = object as? [String: Any] {
            return .dict(d.mapValues { PlistValue.from(object: $0) })
        }
        return .string(String(describing: object))
    }
}

struct Schedule: Identifiable, Equatable, Hashable, Sendable {
    /// Filename stem, e.g. "com.scout.briefing-weekend" — also the plist Label.
    let id: String
    var label: String
    var runnerScript: URL
    var workingDirectory: URL?
    var environment: [String: String]
    var logStdOut: URL?
    var logStdErr: URL?
    var trigger: ScheduleTrigger
    /// Every top-level plist key we don't surface, preserved verbatim for
    /// round-trip. Populated on parse, re-emitted on serialize.
    var unknownKeys: [String: PlistValue]

    init(
        id: String,
        label: String,
        runnerScript: URL,
        workingDirectory: URL? = nil,
        environment: [String: String] = [:],
        logStdOut: URL? = nil,
        logStdErr: URL? = nil,
        trigger: ScheduleTrigger,
        unknownKeys: [String: PlistValue] = [:]
    ) {
        self.id = id
        self.label = label
        self.runnerScript = runnerScript
        self.workingDirectory = workingDirectory
        self.environment = environment
        self.logStdOut = logStdOut
        self.logStdErr = logStdErr
        self.trigger = trigger
        self.unknownKeys = unknownKeys
    }
}
