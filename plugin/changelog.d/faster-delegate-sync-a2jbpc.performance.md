- **A delegate mirror sync is much faster on a large vault, and builds the same mirror**
  (`engine/scout/delegate_mirror.py`, `engine/scout/kb/outbound.py`). Three changes:
  - **One combined matcher.** Finding which hidden notes a file names used to search every note's
    pattern in turn; one pass over the text now finds where any hidden name could start, and only
    those notes' patterns run there. The matching rules (word edges, case folding, accents, the
    shortest term) are unchanged.
  - **A narrower memo key.** A file's scan is reused while the names, aliases, titles, paths and
    quotes it was scanned against are the same, so editing a hidden note's body (a daily note, say)
    no longer makes the next sync rescan every visible file. Renaming a hidden note, or adding an
    alias or title, still does.
  - **Scans across cores.** Text files are scanned in up to 8 processes when there are 200 or more
    to scan; `SCOUT_DELEGATE_SCAN_WORKERS=1` keeps it in one. The results are applied in the same
    order as before, so the commit tree is the same.
  - The first sync after updating scans everything once, since the memo was made by other code.
