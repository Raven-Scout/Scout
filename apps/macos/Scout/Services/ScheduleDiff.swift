import Foundation

enum ScheduleDiff {
    /// Comma-joined list of field labels that differ between `original` and
    /// `edited`. Empty string if nothing changed. Used to generate default
    /// commit messages like `"schedules: update com.scout.x (trigger, env)"`.
    static func summarize(original: Schedule, edited: Schedule) -> String {
        var parts: [String] = []
        if original.runnerScript != edited.runnerScript { parts.append("runner") }
        if !original.trigger.semanticallyEquals(edited.trigger) { parts.append("trigger") }
        if original.environment != edited.environment { parts.append("env") }
        if original.workingDirectory != edited.workingDirectory {
            parts.append("working-dir")
        }
        if original.logStdOut != edited.logStdOut
            || original.logStdErr != edited.logStdErr {
            parts.append("logs")
        }
        if original.unknownKeys != edited.unknownKeys {
            parts.append("advanced")
        }
        return parts.joined(separator: ", ")
    }
}
