import Foundation

enum PlistIOError: Error, Equatable {
    case malformedRoot
    case missingLabel
    case missingProgramArguments
    case idMismatch(labelInFile: String, fileName: String)
}

enum PlistIO {

    /// Keys we surface on `Schedule`. Everything else ends up in `unknownKeys`.
    private static let knownTopLevelKeys: Set<String> = [
        "Label",
        "ProgramArguments",
        "WorkingDirectory",
        "EnvironmentVariables",
        "StandardOutPath",
        "StandardErrorPath",
        "StartCalendarInterval",
        "StartInterval",
    ]

    /// Reads a Scout plist. The file's base name is used as `Schedule.id` and
    /// must match the `Label` key. Weekday is converted from launchd
    /// convention (0/7=Sun ... 6=Sat) to Calendar convention (1=Sun ... 7=Sat).
    static func readSchedule(from url: URL) throws -> Schedule {
        let data = try Data(contentsOf: url)
        guard let root = try PropertyListSerialization.propertyList(
            from: data, options: [], format: nil
        ) as? [String: Any] else {
            throw PlistIOError.malformedRoot
        }

        guard let label = root["Label"] as? String else {
            throw PlistIOError.missingLabel
        }
        let fileStem = url.deletingPathExtension().lastPathComponent
        if label != fileStem {
            throw PlistIOError.idMismatch(labelInFile: label, fileName: fileStem)
        }

        guard let args = root["ProgramArguments"] as? [String], args.count >= 2 else {
            throw PlistIOError.missingProgramArguments
        }
        let runner = URL(fileURLWithPath: args[1])

        let workingDir = (root["WorkingDirectory"] as? String)
            .map { URL(fileURLWithPath: $0) }
        let environment = (root["EnvironmentVariables"] as? [String: String]) ?? [:]
        let logOut = (root["StandardOutPath"] as? String)
            .map { URL(fileURLWithPath: $0) }
        let logErr = (root["StandardErrorPath"] as? String)
            .map { URL(fileURLWithPath: $0) }

        let trigger = parseTrigger(root: root)

        var unknown: [String: PlistValue] = [:]
        for (k, v) in root where !knownTopLevelKeys.contains(k) {
            unknown[k] = PlistValue.from(object: v)
        }

        return Schedule(
            id: fileStem,
            label: label,
            runnerScript: runner,
            workingDirectory: workingDir,
            environment: environment,
            logStdOut: logOut,
            logStdErr: logErr,
            trigger: trigger,
            unknownKeys: unknown
        )
    }

    private static func parseTrigger(root: [String: Any]) -> ScheduleTrigger {
        if let n = root["StartInterval"] as? Int {
            return .interval(seconds: n)
        }
        let dicts: [[String: Any]]
        if let arr = root["StartCalendarInterval"] as? [[String: Any]] {
            dicts = arr
        } else if let single = root["StartCalendarInterval"] as? [String: Any] {
            dicts = [single]
        } else {
            dicts = []
        }
        let fires: [CalendarFire] = dicts.map { d in
            let launchdWeekday = d["Weekday"] as? Int
            return CalendarFire(
                weekday: launchdWeekday.map(launchdToCalendarWeekday),
                hour: d["Hour"] as? Int ?? 0,
                minute: d["Minute"] as? Int ?? 0
            )
        }
        return .calendar(fires)
    }

    /// launchd weekday: 0 and 7 are Sunday, 1=Mon ... 6=Sat.
    /// Calendar weekday: 1=Sun, 2=Mon ... 7=Sat.
    static func launchdToCalendarWeekday(_ launchd: Int) -> Int {
        let zeroIndexed = ((launchd % 7) + 7) % 7  // 0..6, handles 7 and negatives
        return zeroIndexed + 1
    }

    /// Inverse of `launchdToCalendarWeekday`. Calendar 1 (Sun) → launchd 0.
    static func calendarToLaunchdWeekday(_ calendar: Int) -> Int {
        calendar - 1  // 0..6
    }

    /// Serializes a `Schedule` to an XML plist at `url`, atomically. Unknown
    /// keys are written verbatim alongside the surfaced fields.
    static func writeSchedule(_ schedule: Schedule, to url: URL) throws {
        var dict: [String: Any] = [:]
        dict["Label"] = schedule.label
        dict["ProgramArguments"] = ["/bin/bash", schedule.runnerScript.path]
        if let wd = schedule.workingDirectory {
            dict["WorkingDirectory"] = wd.path
        }
        if !schedule.environment.isEmpty {
            dict["EnvironmentVariables"] = schedule.environment
        }
        if let out = schedule.logStdOut {
            dict["StandardOutPath"] = out.path
        }
        if let err = schedule.logStdErr {
            dict["StandardErrorPath"] = err.path
        }
        switch schedule.trigger {
        case .interval(let seconds):
            dict["StartInterval"] = seconds
        case .calendar(let fires):
            dict["StartCalendarInterval"] = fires.map { fire -> [String: Any] in
                var d: [String: Any] = ["Hour": fire.hour, "Minute": fire.minute]
                if let w = fire.weekday {
                    d["Weekday"] = calendarToLaunchdWeekday(w)
                }
                return d
            }
        }
        for (k, v) in schedule.unknownKeys {
            dict[k] = v.toObject()
        }

        let data = try PropertyListSerialization.data(
            fromPropertyList: dict, format: .xml, options: 0
        )
        try atomicWrite(data: data, to: url)
    }

    private static func atomicWrite(data: Data, to url: URL) throws {
        let dir = url.deletingLastPathComponent()
        let tmp = dir.appendingPathComponent(
            ".\(url.lastPathComponent).tmp.\(UUID().uuidString)"
        )
        try data.write(to: tmp, options: .atomic)
        if FileManager.default.fileExists(atPath: url.path) {
            _ = try FileManager.default.replaceItemAt(url, withItemAt: tmp)
        } else {
            try FileManager.default.moveItem(at: tmp, to: url)
        }
    }
}
