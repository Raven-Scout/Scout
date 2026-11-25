- **A delegate mirror's privacy scan is about 10× faster on large notes** (`engine/scout/kb/outbound.py`). Each
  private note's pattern was searched over the whole text, so a 2.4 MB note took about a minute. Every match starts
  with the first word of one of the note's names, as written, so the pattern is now tried only where such a word
  starts. It finds exactly the same lines: a differential test checks that hit for hit over adversarial and fuzzed text.
