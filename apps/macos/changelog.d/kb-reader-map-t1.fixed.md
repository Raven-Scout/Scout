- **The Knowledge Base reader and map hold up at 1280 pt and on a big vault (UI audit T1).**
  - **The note header no longer collapses in a narrow reader.** The path truncates in the middle (folders
    first, then the note's name), Reload is an icon, Save and the Read | Rich | Source switch keep their size,
    and when there isn't room for both the controls move to their own row above the path. Before, with the
    sidebar, file tree and Links panel open at 1280 pt, the path wrapped a letter per line and Save stacked
    vertically, which could push the page up under the title bar.
  - **The Links panel can be hidden,** with a button in the note's header, and it starts hidden on a page
    narrower than about 1180 pt (a window under about 1400 pt). Your choice is kept.
  - **A note with an unsaved draft says so.** Opening it shows an "Unsaved draft" banner, in every mode, with
    Save and Discard Edits…; notes with drafts get a dot in the file tree, and so does a closed folder holding
    one. The home button and the path's root ask before leaving a note with a draft: keep the draft and leave
    (the default), discard the edits, or stay.
  - **A change on disk under unsaved edits shows at once,** in a banner naming the note, with Save My Version
    and Reload… (which asks before discarding). The check also runs when you switch back to Scout.
  - **The map no longer comes back blank.** It drew only on animation frames, which WebKit holds while it
    treats the page as hidden, so a map could have all its notes and never draw them. It now draws as soon as
    its notes arrive and again whenever the page is shown; a map page that hasn't started within a few seconds
    of being shown is reloaded and given back what it showed. A new test hosts the real page in a window and
    checks it is drawn on a first visit, after opening a note with a draft, and after leaving and coming back.
  - **The map's Notes and Explore panels collapse,** each with its own chevron, and start collapsed on a
    narrow map; selecting a note opens the side panel again. The map frames the notes in the space the panels
    leave free, measured from the panels as drawn, instead of squeezing a 1,400-note vault into a 100 pt
    cluster behind them.
  - **The map reopens on its last layout after a relaunch** (kept in `~/Library/Caches/Scout/kb-map/`, one
    file per vault), so a big vault no longer lays itself out from scratch on the first visit of every launch.
    Positions that aren't finite numbers are dropped, so they can't make the map's load unencodable.
