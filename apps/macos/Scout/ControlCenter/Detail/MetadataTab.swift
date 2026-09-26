import SwiftUI

struct MetadataTab: View {
    let run: Run

    var body: some View {
        ScrollView {
            Text(prettyJSON)
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
        }
    }

    private var prettyJSON: String {
        let info: [String: String] = [
            "id": run.id,
            "type": run.type.rawValue,
            "runnerScript": run.runnerScript,
            "source": run.source.rawValue,
            "status": run.status.rawValue,
            "startedAt": run.startedAt.formatted(date: .abbreviated, time: .standard),
            "endedAt": run.endedAt?.formatted(date: .abbreviated, time: .standard) ?? "—",
            "exitCode": run.exitCode.map(String.init) ?? "—",
            "cost": run.cost.map { "$\($0 as NSDecimalNumber)" } ?? "—",
            "logPath": run.logPath.path,
            "logSizeBytes": String(run.logSizeBytes),
            "errorsCount": String(run.errorsDetected.count),
            "commitsCount": String(run.commits.count),
            "retryOf": run.retryOf ?? "—"
        ]
        let sorted = info.sorted { $0.key < $1.key }
        return sorted.map { "\($0.key): \($0.value)" }.joined(separator: "\n")
    }
}
