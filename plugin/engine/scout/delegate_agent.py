"""The agent's side of a delegate mirror (#550 V3): ``scoutctl delegate init``.

Run inside a clone of a delegate mirror, on the agent's platform (a container
with shell and Python, such as Grok Bot's computer). It reads nothing but the
clone and writes nothing in its tree, so a sync's ``git pull`` never meets a
file it made:

- **Finds the clone** (``--clone``, else ``SCOUT_DATA_DIR``, else the working
  directory or a folder above it) by its manifest,
  ``.scout-state/delegate-mirror.json``, and the delegate it was built for. It
  refuses the owner's own vault (the one holding the link state) and a folder
  that isn't a mirror.
- **Checks** that the clone's ``scout-config.yaml`` lists that delegate, that
  ``SCOUT_READER`` (when set) is that delegate and ``SCOUT_MODE`` isn't set,
  and that this engine is at least the one that built the mirror.
- **Reads as the delegate** once (what ``kb files`` lists, without writing the
  privacy index), to show the engine works here.
- **Writes the environment** Scout needs here to ``<git dir>/scout-delegate.env``
  (inside ``.git``, never tracked), and with ``--print-env`` prints it as shell
  ``export`` lines for ``eval``.

It is safe to run again, as the guide asks at the start of every task: on Grok
Bot a computer update drops installed tools, and ``init`` is how the agent
finds out.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import scout
from scout.delegate_mirror import GUIDE_PATHS, MANIFEST_PATH, STATE_REL, _check_id
from scout.repo_sync import RepoSyncError

ENV_FILE = "scout-delegate.env"  # in the clone's git directory


@dataclass
class InitResult:
    delegate: str
    clone: str
    env: dict[str, str]
    files: int
    env_file: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "delegate": self.delegate,
            "clone": self.clone,
            "env": self.env,
            "files": self.files,
            "env_file": self.env_file,
            "notes": self.notes,
        }

    def exports(self) -> str:
        """The environment as shell lines, for ``eval "$(scoutctl delegate init --print-env)"``."""
        lines = [f"export {k}={shlex.quote(v)}" for k, v in self.env.items()]
        return "\n".join([*lines, "unset SCOUT_MODE"]) + "\n"


def find_clone(start: Path) -> Path | None:
    """The delegate mirror clone at ``start`` or a folder above it, by its manifest."""
    here = start.resolve()
    for folder in (here, *here.parents):
        if (folder / MANIFEST_PATH).is_file():
            return folder
    return None


def _version(text: str) -> tuple[int, ...]:
    """``0.18.16`` as ``(0, 18, 16)``; anything after the numbers (``rc.1``, ``.dev0``) is ignored."""
    return tuple(int(n) for n in re.findall(r"\d+", text.split("-")[0].split("+")[0])[:3])


def _git_dir(clone: Path) -> Path | None:
    proc = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "--absolute-git-dir"], capture_output=True, text=True, check=False
    )
    return Path(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else None


def init(clone: Path | None = None, *, env: Mapping[str, str] | None = None) -> InitResult:
    """Check a delegate mirror clone and the engine in it, and write the environment Scout needs there."""
    env = os.environ if env is None else env
    start = clone or (Path(env["SCOUT_DATA_DIR"]) if env.get("SCOUT_DATA_DIR") else Path.cwd())
    root = find_clone(start)
    if root is None:
        raise RepoSyncError(
            "not_a_mirror",
            f"refused: {start} isn't in a delegate mirror clone (no {MANIFEST_PATH}); clone the mirror the owner "
            "gave you and run this inside it",
        )
    if (root / STATE_REL).exists():
        raise RepoSyncError(
            "owners_vault",
            f"refused: {root} is the owner's vault (it holds {STATE_REL}), not a mirror clone; delegate init runs "
            "in the agent's clone of its mirror",
        )
    try:
        manifest = json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))
        delegate = str(manifest["delegate"])
        built_by = str(manifest.get("engine_version", ""))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RepoSyncError("not_a_mirror", f"refused: {MANIFEST_PATH} in {root} can't be read ({e})") from e
    _check_id(delegate)
    reader = f"delegate:{delegate}"
    if env.get("SCOUT_READER") and env["SCOUT_READER"] != reader:
        raise RepoSyncError(
            "reader_mismatch",
            f"refused: SCOUT_READER is {env['SCOUT_READER']}, but this mirror is {reader}'s; unset it, or use "
            "the clone of that delegate's own mirror",
        )

    from scout.kb.privacy import load_policy, visible_to
    from scout.kb.read import all_labels

    policy = load_policy(root)
    if policy.delegate(reader) is None:
        raise RepoSyncError(
            "delegate_not_listed",
            f"refused: this clone's scout-config.yaml doesn't list {delegate}, so Scout would show it nothing; "
            "pull the mirror again (git pull --ff-only), or clone it again",
        )
    notes: list[str] = []
    if env.get("SCOUT_MODE"):
        notes.append(
            f"SCOUT_MODE is set ({env['SCOUT_MODE']}): an agent's task isn't a scheduled run; the exports unset it"
        )
    if built_by and _version(scout.__version__) < _version(built_by):
        notes.append(
            f"this engine is {scout.__version__}, older than the {built_by} that built the mirror; upgrade it: "
            "uv tool upgrade scoutctl"
        )
    missing = [rel for rel in GUIDE_PATHS if not (root / rel).is_file()]
    if missing:
        notes.append(f"the guide ({', '.join(missing)}) is missing; pull the mirror again (git pull --ff-only)")
    labels = all_labels(root, policy, persist=False)  # no privacy index written: init leaves the tree as it found it
    files = sum(visible_to(label, reader, policy) for label in labels.values())
    if files == 0:
        notes.append(f"{delegate} sees no files in this clone; check the clone, or ask the owner to sync the mirror")
    result = InitResult(
        delegate=delegate, clone=str(root), env={"SCOUT_DATA_DIR": str(root), "SCOUT_READER": reader}, files=files
    )
    git_dir = _git_dir(root)
    if git_dir is not None and git_dir.is_dir():
        path = git_dir / ENV_FILE
        path.write_text("".join(f"{k}={v}\n" for k, v in result.env.items()), encoding="utf-8")
        result.env_file = str(path)
    else:
        notes.append("not a git checkout, so nothing was written; load the environment with --print-env each task")
    result.notes = notes
    return result
