import SwiftUI

/// Per-tool breakdown of how Scout actually spent its turn — counts at the
/// top, then a chronological list of every Bash / Read / Edit / etc. call
/// with its inputs collapsed to one line.
struct ToolsTab: View {
    let run: Run
    @EnvironmentObject var state: AppState
    @State private var activity: ClaudeSessionActivity? = nil
    @State private var didLoad = false
    @State private var filter: String? = nil
    @State private var search: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !didLoad {
                Spacer()
                Text("Loading…").foregroundStyle(.secondary)
                Spacer()
            } else if let a = activity, !a.calls.isEmpty {
                summaryStrip(a)
                Divider()
                callList(a)
            } else if didLoad {
                Spacer()
                Text("No tool calls recorded for this run.")
                    .foregroundStyle(.secondary)
                    .italic()
                Spacer()
            }
        }
        .task(id: run.id) {
            activity = await state.claudeSessionService.activity(for: run)
            didLoad = true
        }
    }

    private func summaryStrip(_ activity: ClaudeSessionActivity) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                chip(name: "All", count: activity.calls.count, isSelected: filter == nil) {
                    filter = nil
                }
                ForEach(activity.byTool, id: \.name) { entry in
                    chip(name: entry.name, count: entry.count, isSelected: filter == entry.name) {
                        filter = entry.name
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
        }
    }

    private func chip(name: String, count: Int, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(name).font(.system(.callout, design: .monospaced))
                Text("\(count)")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(isSelected ? Color.accentColor.opacity(0.25) : Color.secondary.opacity(0.10))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(isSelected ? Color.accentColor : Color.clear, lineWidth: 1)
            )
        }
        .buttonStyle(.plainHit)
    }

    private func callList(_ activity: ClaudeSessionActivity) -> some View {
        let calls = activity.calls.filter { call in
            (filter == nil || call.name == filter)
            && (search.isEmpty
                || call.summary.localizedCaseInsensitiveContains(search)
                || call.name.localizedCaseInsensitiveContains(search))
        }
        return VStack(alignment: .leading, spacing: 0) {
            TextField("Search tool calls…", text: $search)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 320)
                .padding(.horizontal, 12)
                .padding(.top, 8)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(calls) { call in
                        callRow(call)
                        Divider().opacity(0.4)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
            }
        }
    }

    private func callRow(_ call: ClaudeSessionActivity.ToolCall) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(call.name)
                .font(.system(.caption, design: .monospaced).weight(.semibold))
                .foregroundStyle(color(for: call.name))
                .frame(width: 90, alignment: .leading)
            Text(call.summary)
                .font(.system(.callout, design: .monospaced))
                .lineLimit(2)
                .truncationMode(.middle)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let ts = call.timestamp {
                Text(ts, format: .dateTime.hour().minute().second())
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 6)
    }

    private func color(for name: String) -> Color {
        switch name {
        case "Bash":        return .orange
        case "Read":        return .blue
        case "Edit":        return .yellow
        case "Write":       return .green
        case "Grep", "Glob": return .purple
        case "WebFetch", "WebSearch": return .pink
        case "TodoWrite":   return .gray
        default:            return .secondary
        }
    }
}
