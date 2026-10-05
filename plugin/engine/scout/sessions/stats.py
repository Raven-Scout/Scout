"""Work counters that one index build fills in (1b spec §3.7). Not part of the index schema."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BuildStats:
    desktop_decoded: int = 0  # desktop records read and decoded
    desktop_served_last_good: int = 0  # decode failures answered from the desktop cache
    transcripts_full_parsed: int = 0  # transcripts parsed from byte 0
    transcripts_tail_parsed: int = 0  # transcripts parsed from a checkpoint
    transcript_bytes_read: int = 0  # bytes those parses read (not the ≤ 4 KB identity check or first-prompt head)
    caches_written: list[str] = field(default_factory=list)  # "desktop" / "transcripts" / "prs"


__all__ = ["BuildStats"]
