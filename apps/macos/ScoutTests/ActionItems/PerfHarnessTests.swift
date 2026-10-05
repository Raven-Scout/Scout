import Testing
import Foundation
import Combine
import SwiftUI
import AppKit
@testable import Scout

/// Opt-in performance harness. Skipped unless `SCOUT_PERF_FILE` points at a
/// real action-items markdown file, so CI never runs it.
///
/// Usage (the `TEST_RUNNER_` prefix is what forwards the variable into the
/// test process, and the selector must be the type name — a suite name or a
/// bare variable both run zero tests and still report success):
///   TEST_RUNNER_SCOUT_PERF_FILE=/path/to/action-items-YYYY-MM-DD.md \
///     xcodebuild test -project Scout.xcodeproj -scheme Scout \
///       -destination 'platform=macOS' -only-testing:ScoutTests/PerfHarnessTests
@Suite("PerfHarness")
@MainActor
struct PerfHarnessTests {
    static var perfFile: URL? {
        guard let p = ProcessInfo.processInfo.environment["SCOUT_PERF_FILE"],
              FileManager.default.fileExists(atPath: p) else { return nil }
        return URL(fileURLWithPath: p)
    }

    static func ms(_ block: () throws -> Void) rethrows -> Double {
        let t0 = DispatchTime.now().uptimeNanoseconds
        try block()
        return Double(DispatchTime.now().uptimeNanoseconds - t0) / 1_000_000
    }

    @Test func measureParseAndLayout() throws {
        guard let url = Self.perfFile else {
            print("PERF: SCOUT_PERF_FILE unset — skipping")
            return
        }

        var data = Data()
        let readMS = try Self.ms { data = try Data(contentsOf: url) }
        var text = ""
        let decodeMS = Self.ms { text = String(data: data, encoding: .utf8) ?? "" }

        // Parse — three runs so we can see cache/warm effects.
        var doc: ActionItemsDocument!
        var parseRuns: [Double] = []
        for _ in 0..<3 {
            let m = try Self.ms {
                doc = try ActionItemsParser.parse(
                    text: text, sourceURL: url, sourceBytes: data.count
                )
            }
            parseRuns.append(m)
        }

        let liveTasks = doc.sections.reduce(0) { $0 + $1.tasks.count }

        print("""
        PERF ── input
          file            \(url.lastPathComponent)
          bytes           \(data.count)
          sections        \(doc.sections.count)
          tasks           \(liveTasks)
        PERF ── parse
          read            \(String(format: "%.1f", readMS)) ms
          decode          \(String(format: "%.1f", decodeMS)) ms
          parse run1      \(String(format: "%.1f", parseRuns[0])) ms
          parse run2      \(String(format: "%.1f", parseRuns[1])) ms
          parse run3      \(String(format: "%.1f", parseRuns[2])) ms
        """)

        // View construction + layout: host the real SectionViews the way
        // ActionItemsView does (eager VStack, #83, behind a TaskWindow) and
        // force a layout pass. Then the same with every row built — what the
        // tab cost before windowing, and what it would cost now with every
        // section fully expanded via "Show more".
        let windowed = Self.layout(doc, url: url, window: { _ in TaskWindow() })
        let everyRow = Self.layout(doc, url: url, window: { section in
            var w = TaskWindow()
            while w.hiddenCount(in: section.tasks) > 0 { w.showMore() }
            return w
        })

        print("""
        PERF ── view (windowed, what the tab builds on open)
          construct       \(String(format: "%.1f", windowed.constructMS)) ms
          layout          \(String(format: "%.1f", windowed.layoutMS)) ms
          fittingSize     \(windowed.height) pt
        PERF ── view (every row built)
          construct       \(String(format: "%.1f", everyRow.constructMS)) ms
          layout          \(String(format: "%.1f", everyRow.layoutMS)) ms
          fittingSize     \(everyRow.height) pt
        PERF ── total first paint (windowed)
          \(String(format: "%.1f", readMS + decodeMS + parseRuns[0] + windowed.constructMS + windowed.layoutMS)) ms
        """)
    }

    static func layout(
        _ doc: ActionItemsDocument,
        url: URL,
        window: (ActionSection) -> TaskWindow
    ) -> (constructMS: Double, layoutMS: Double, height: CGFloat) {
        let noop: @MainActor (WriteOp, Int?) async throws -> Void = { _, _ in }
        let scoutDir = url.deletingLastPathComponent().deletingLastPathComponent()
        let windows = Dictionary(uniqueKeysWithValues: doc.sections.map { ($0.id, window($0)) })

        let root = VStack(alignment: .leading, spacing: 0) {
            ForEach(doc.sections) { section in
                SectionView(
                    section: section,
                    displayedDate: Date(),
                    scoutDirectory: scoutDir,
                    selection: nil,
                    window: windows[section.id] ?? TaskWindow(),
                    onShowMore: {},
                    onOp: noop
                )
            }
        }
        .frame(width: 900)

        var host: NSHostingView<AnyView>!
        let constructMS = ms {
            host = NSHostingView(rootView: AnyView(root))
        }
        let layoutMS = ms {
            host.frame = NSRect(x: 0, y: 0, width: 900, height: 100_000)
            host.layoutSubtreeIfNeeded()
            _ = host.fittingSize
        }
        return (constructMS, layoutMS, host.fittingSize.height)
    }

    /// One write op on main: how many parses and rebuilds does it cost?
    @Test func measureWriteOpCost() async throws {
        guard let src = Self.perfFile else {
            print("PERF: SCOUT_PERF_FILE unset — skipping")
            return
        }

        // Work on a copy in tmp so the real vault is never written.
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let stem = src.deletingPathExtension().lastPathComponent
            .replacingOccurrences(of: "action-items-", with: "")
        let dst = dir.appendingPathComponent("action-items-\(stem).md")
        try FileManager.default.copyItem(at: src, to: dst)

        let comps = stem.split(separator: "-").compactMap { Int($0) }
        guard comps.count == 3 else { return }
        let date = Calendar(identifier: .iso8601).date(from: DateComponents(
            timeZone: TimeZone.current,
            year: comps[0], month: comps[1], day: comps[2]
        ))!

        let service = ActionItemsDocumentService(directory: dir, fileEvents: NoopFS())

        var publishCount = 0
        let sink = service.$state.sink { _ in publishCount += 1 }
        defer { sink.cancel() }

        let loadMS = try await Self.msAsync { try await service.load(date: date) }
        let afterLoad = publishCount

        // This is exactly what handleOp does after a successful CLI write.
        let reparseMS = try await Self.msAsync { await service.reparseCurrent() }
        let afterReparse = publishCount

        print("""
        PERF ── write op
          initial load        \(String(format: "%.1f", loadMS)) ms  (\(afterLoad) publishes)
          reparseCurrent()    \(String(format: "%.1f", reparseMS)) ms  (\(afterReparse - afterLoad) publishes)
          note: FSEvents fires a SECOND reparse ~250ms later in the real app,
                so a click costs roughly 2x the reparseCurrent figure.
        """)
    }

    static func msAsync(_ block: () async throws -> Void) async rethrows -> Double {
        let t0 = DispatchTime.now().uptimeNanoseconds
        try await block()
        return Double(DispatchTime.now().uptimeNanoseconds - t0) / 1_000_000
    }
}
