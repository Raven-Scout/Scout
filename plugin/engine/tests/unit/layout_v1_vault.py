"""A layout-v1 vault (0.18-shaped) with every row of the layout v2 path map, for the layout move tests.

Anonymized: people Alex, Priya and Sam; generic project and organization names.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PERSON = """---
name: Alex Example
type: person
aliases: [Alex]
email: alex@example.com
role: Engineer
relationships:
  - type: works_on
    target: "[[projects/apollo/apollo]]"
  - type: employed_by
    target: "[[ontology/entities/acme]]"
---

# Alex Example

Works on [[projects/apollo/apollo|Apollo]].
"""

PRIVATE_PERSON = """---
name: Sam Private
type: person
visibility: private
email: sam@example.com
---

# Sam Private

A private person.
"""

ORG = """---
name: Acme
type: organization
domain: example.com
industry: software
---

# Acme

The vendor.
"""

PROJECT = """---
name: Apollo
type: project
---

# Apollo

Owner: [[people/alex-example|Alex]]. Run `scripts/scoutctl kb read knowledge-base/projects/apollo/apollo.md`.
Health in [[connector-health]] and `knowledge-base/connector-health.md`.
"""

ROOT_REGISTRY = """# Meetings
**Parent:** [[knowledge-base]]

## Active Recurring Meetings

| Meeting | Frequency | Day/Time | Key Attendees | Projects |
|---------|-----------|----------|---------------|----------|
| [[meetings/standup/standup\\|Standup]] | daily | 09:00 | Alex, Priya | Apollo |
| [[meetings/review/review\\|Review]] | weekly | Fri | Sam | |
"""

KB_REGISTRY = """# Meetings

## Active Recurring Meetings

| Meeting | Frequency | Day/Time | Key Attendees | Projects |
|---------|-----------|----------|---------------|----------|
| [[meetings/standup/standup\\|Standup]] | daily | 09:30 | Alex | Apollo |
| [[meetings/planning/planning\\|Planning]] | monthly | Mon | Priya | |
"""

CONFIG = """user:
  name: Alex
  email: alex@example.com
instance:
  name: Scout
  name_lower: scout
platform: macos
plugin:
  version_at_last_setup: 0.18.0
  version_at_last_update: 0.18.0
  applied_migrations: []
kb_lint:
  exclude_globs:
    - "knowledge-base/projects/**/archive/**"
kb_structure:
  projects_dir: knowledge-base/projects
"""


def _w(vault: Path, rel: str, text: str, *, mode: int | None = None) -> None:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mode is not None:
        p.chmod(mode)


def _git(vault: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-11-01T10:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-11-01T10:00:00+00:00",
    }
    return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True, env=env).stdout


def build_v1_vault(vault: Path, *, spaces: str | None = None, dirty: bool = True, git: bool = True) -> Path:
    """Write the vault and commit it. ``spaces``: a ``spaces:`` YAML block to add to the config."""
    vault.mkdir(parents=True, exist_ok=True)
    _w(vault, "scout-config.yaml", CONFIG + (spaces or ""))
    for kind in ("SKILL", "DREAMING", "RESEARCH", "RESPOND"):
        _w(
            vault,
            f"{kind}.md",
            f"# {kind}\n\n**BASE_DIR:** `{vault}`\n\n"
            "Run `scripts/scoutctl kb lint`. Read {SCOUT_DIR}/SKILL.md.\n".replace("{SCOUT_DIR}", str(vault)),
        )
    for runner in ("run-scout.sh", "run-dreaming.sh", "run-research.sh", "run-respond.sh", "run-mine.sh"):
        _w(
            vault,
            runner,
            f'#!/bin/bash\nSCOUT_DIR="{vault}"\n"$SCOUT_DIR/scripts/scout-lock.sh"\ncat "$SCOUT_DIR/SKILL.md"\n',
            mode=0o755,
        )
    _w(vault, "scripts/scoutctl", '#!/bin/bash\nexec scoutctl "$@"\n', mode=0o755)
    _w(vault, "scripts/scout-lock.sh", "#!/bin/bash\n:\n", mode=0o755)
    _w(vault, "scripts/heartbeat.sh", '#!/bin/bash\nSCOUT_DIR="$(cd "$(dirname "$0")/.." && pwd)"\n', mode=0o755)
    _w(vault, "scripts/my-own.py", "print('mine')\n")
    _w(vault, "hooks/kb-pre-filter.sh", '#!/bin/bash\n"$SCOUT_DIR/scripts/scoutctl" kb\n', mode=0o755)
    _w(vault, "docs/kb-page-standard.md", "# KB page standard\n")
    _w(vault, "docs/wishlist/w-1.md", "---\ntitle: one\n---\n")
    _w(vault, "docs/other.md", "# a user doc\n")
    _w(vault, "dreaming-proposals.md", "# Dreaming proposals\n\nSee knowledge-base/session-log.md.\n")
    _w(vault, "inbox.md", "# Inbox\n")
    _w(vault, "drop/README.md", "Drop files here.\n")
    _w(vault, "drop/big.pdf", "not really a pdf\n")
    _w(
        vault,
        ".gitignore",
        ".scout-logs/\n.scout-cache/\n.scout-state/last-assembled/\n*.proposed-merge\ndrop/*\n!drop/README.md\n",
    )
    kb = "knowledge-base"
    _w(vault, f"{kb}/knowledge-base.md", "# KB\n\n- [[projects/projects]]\n- [[meetings/meetings]]\n")
    _w(vault, f"{kb}/people.md", "# People\n\n- [[people/alex-example]]\n")
    _w(vault, f"{kb}/session-log.md", "# Session log\n")
    _w(vault, f"{kb}/session-log/2026-11.md", "# November\n")
    _w(vault, f"{kb}/scout-mistake-audit.md", "# Audit\n")
    _w(vault, f"{kb}/scout-mistake-audit/2026-11.md", "# Audit Nov\n")
    _w(vault, f"{kb}/connector-health.md", "# Connector health\n")
    _w(vault, f"{kb}/review-queue.md", "# Review\n")
    _w(vault, f"{kb}/projects/projects.md", "# Projects\n\n- [[projects/apollo/apollo]]\n")
    _w(vault, f"{kb}/projects/apollo/apollo.md", PROJECT)
    _w(vault, f"{kb}/topics/topics.md", "# Topics\n")
    _w(vault, f"{kb}/topics/cooking.md", "# Cooking\n\nSee [[sources/2026-11-01-recipe]].\n")
    _w(vault, f"{kb}/sources/sources.md", "# Sources\n")
    _w(vault, f"{kb}/sources/2026-11-01-recipe.md", "# Recipe\n\nFrom [[projects/apollo/apollo]].\n")
    _w(vault, f"{kb}/people/alex-example.md", PERSON)
    _w(vault, f"{kb}/people/sam-private.md", PRIVATE_PERSON)
    _w(vault, f"{kb}/people/team-notes.md", "---\nname: Team\ntype: team\n---\n")
    _w(vault, f"{kb}/ontology/entities/acme.md", ORG)
    _w(vault, f"{kb}/ontology/entities/rust.md", "---\nname: Rust\ntype: technology\n---\n")
    _w(vault, f"{kb}/personal/journal.md", "# Journal\n\nSee [[personal/svj-board]].\n")
    _w(vault, f"{kb}/personal/svj-board.md", "# Board\n")
    _w(vault, f"{kb}/personal/svj-repairs.md", "# Repairs\n")
    _w(vault, f"{kb}/personal/svj-budget/2026.md", "# Budget\n")
    _w(vault, f"{kb}/inbox/README.md", "This is not the inbox.\n")
    _w(vault, "meetings/meetings.md", ROOT_REGISTRY)
    _w(vault, "meetings/standup/standup.md", "---\ncalendar_match: [standup]\n---\n# Standup\n")
    _w(vault, "meetings/standup/2026-11-02.md", "# Standup 2026-11-02\n\nroot copy\n")
    _w(vault, "meetings/standup/2026-11-03.md", "# same on both sides\n")
    _w(vault, "meetings/review/review.md", "# Review\n")
    _w(vault, f"{kb}/meetings/meetings.md", KB_REGISTRY)
    _w(vault, f"{kb}/meetings/standup/2026-11-02.md", "# Standup 2026-11-02\n\nkb copy, newer\n")
    _w(vault, f"{kb}/meetings/standup/2026-11-03.md", "# same on both sides\n")
    _w(vault, f"{kb}/meetings/planning/planning.md", "# Planning\n")
    _w(vault, "action-items/action-items-2026-11-20.md", "# Today\n\n- [ ] Read [[projects/apollo/apollo]]\n")
    _w(vault, "action-items/archive/action-items-2026-01-01.md", "# Old\n\n- [x] [[projects/apollo/apollo]]\n")
    (vault / "action-items/meeting-prep").mkdir(parents=True, exist_ok=True)
    _w(vault, "notes-of-mine.md", "# a root file Scout didn't make\n")
    # untracked state with a merge base for a managed file, and a parked drift copy
    _w(vault, ".scout-state/last-rendered/scripts/scoutctl", '#!/bin/bash\nexec scoutctl "$@"\n')
    _w(vault, ".scout-state/drift/hooks/kb-pre-filter.sh.vault", "old copy\n")
    if not git:
        return vault
    _git(vault, "init", "-q", "-b", "main")
    _git(vault, "config", "user.email", "scout@example.com")
    _git(vault, "config", "user.name", "Scout Bot")
    _git(vault, "add", "-A")
    _git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "v1 vault")
    # the kb side's standup file is newer than the root side's
    _w(vault, f"{kb}/meetings/standup/2026-11-02.md", "# Standup 2026-11-02\n\nkb copy, newer\n\nedited\n")
    env_date = "2026-11-05T10:00:00+00:00"
    subprocess.run(
        ["git", "-C", str(vault), "-c", "commit.gpgsign=false", "commit", "-q", "-am", "newer kb standup"],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_DATE": env_date, "GIT_COMMITTER_DATE": env_date},
    )
    if dirty:
        _w(vault, "inbox.md", "# Inbox\n\n- an uncommitted note\n")
    return vault
