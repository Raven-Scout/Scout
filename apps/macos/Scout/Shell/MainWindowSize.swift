import CoreGraphics

/// The main window's minimum size, read by `ScoutApp` and by the layout tests
/// (`LayoutSize`), so the size the tests prove pages fit at is the one the
/// window enforces.
///
/// 1070 pt is the narrowest every page lays out at with the sidebar open
/// (UI audit #8/#98). The Sessions header sets it: with a session selected,
/// the board beside the 340 pt compact detail pane must hold the header's
/// ~503 pt (page 849 = 1070 − the 221 pt sidebar). Without a selection
/// Sessions (~818 pt) and Control Center (~790 pt) would allow ~1040.
/// Going lower needs the Sessions header reworked first; `PageLayoutTests`
/// fails if a page stops fitting.
enum MainWindowSize {
    static let minWidth: CGFloat = 1070
    static let minHeight: CGFloat = 640
}
