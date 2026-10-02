"""Three-way merge wrapper around `git merge-file`.

Used by stage 5 of the bootstrap pipeline to merge plugin-side phase
updates with vault-side edits to SKILL.md / DREAMING.md / RESEARCH.md.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MergeResult:
    """Outcome of a three-way merge.

    - ``content``: the merged text. If ``conflicts`` is True, the text
      contains conflict markers (``<<<<<<< ours``, ``=======``,
      ``>>>>>>> theirs``, with diff3-style ``||||||| base`` blocks).
    - ``conflicts``: whether any conflicts were left unresolved.
    """

    content: str
    conflicts: bool


class MergeUnavailable(RuntimeError):
    """git merge-file could not run at all: git is missing, timed out, or failed.

    Distinct from a merge that ran and found conflicts, so a caller can tell
    "install git" from "merge by hand".
    """


def three_way_merge(*, base: str, ours: str, theirs: str, labels: tuple[str, str, str] | None = None) -> MergeResult:
    """Merge ``ours`` and ``theirs`` against common ancestor ``base``.

    Wraps ``git merge-file --diff3 -p`` which is shipped with every
    git installation. Exit code 0 = clean; >0 = number of conflicts.
    ``labels`` names the ours/base/theirs sides in the conflict markers;
    without it git prints the temporary file paths.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ours_path = tmp_path / "ours"
        base_path = tmp_path / "base"
        theirs_path = tmp_path / "theirs"
        # surrogateescape: text read with it (vault files) keeps any byte that
        # is not valid UTF-8 through the merge unchanged.
        ours_path.write_text(ours, encoding="utf-8", errors="surrogateescape")
        base_path.write_text(base, encoding="utf-8", errors="surrogateescape")
        theirs_path.write_text(theirs, encoding="utf-8", errors="surrogateescape")

        label_args = [arg for label in labels for arg in ("-L", label)] if labels else []
        try:
            proc = subprocess.run(
                [
                    "git",
                    "merge-file",
                    "--diff3",
                    "-p",
                    *label_args,
                    str(ours_path),
                    str(base_path),
                    str(theirs_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="surrogateescape",
                # A wedged git (e.g. waiting on a lock) must not hang
                # bootstrap upgrade forever; merge-file on three small text
                # files is sub-second, so 30s is generous (#47).
                timeout=30,
            )
        except subprocess.TimeoutExpired as e:
            raise MergeUnavailable("git merge-file timed out after 30s") from e
        except OSError as e:
            raise MergeUnavailable(f"could not run git merge-file ({type(e).__name__}: {e})") from e
        # git merge-file: returncode 0 = clean, 1..127 = conflict count,
        # 128/255 = fatal git error. Treat fatal as "raise".
        if proc.returncode < 0 or proc.returncode > 127:
            raise MergeUnavailable(f"git merge-file exited {proc.returncode}: {proc.stderr.strip()}")
        return MergeResult(content=proc.stdout, conflicts=proc.returncode > 0)
