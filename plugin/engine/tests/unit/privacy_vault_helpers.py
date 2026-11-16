"""Build the anonymised privacy fixture vault (#453, spec §11.1).

``tests/fixtures/privacy-vault/`` holds the files; :func:`build_privacy_vault`
copies them into a temp dir and makes a working vault of them:

- the sentinel dates (``2000-01-01`` / ``2000-06-30`` / ``2000-12-31``) become
  dates around today, so the permit task's ``surface_rule`` window covers the
  day the test runs;
- ``action-items/action-items-<today>.md`` is seeded with one normal item;
- the engine's ontology schema goes to ``knowledge-base/ontology/schema.yaml``
  and the ``knowledge_base`` → ``knowledge-base`` symlink is made, as in a real
  vault;
- a git history: the seed commit, then the three §11.1 commits
  (:data:`COMMITS`): one touching only private files, one touching a private
  and a normal file, and a normal one.

Stand-ins only (Alex / Priya / Sam, ``example-org``, ``PROJ-1234``). Every
private file carries a unique ``CANARY-PRIV-<n>`` token (:data:`CANARIES`).
Git runs with a fixed identity and no global or system config, so the user's
git setup (signing, hooks, default branch) can't change the result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import scout

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "privacy-vault"
ENGINE_SCHEMA = Path(scout.__file__).resolve().parent / "kb" / "schema.yaml"

# Vault-relative path → its canary token.
CANARIES: dict[str, str] = {
    "knowledge-base/personal/task-renew-permit.md": "CANARY-PRIV-1R7K",
    "knowledge-base/people/sam.md": "CANARY-PRIV-2S4M",
    "knowledge-base/topics/billing/billing.md": "CANARY-PRIV-3B5L",
    "knowledge-base/topics/misc/draft.md": "CANARY-PRIV-4D6R",
    "knowledge-base/topics/misc/broken.md": "CANARY-PRIV-5K7B",
    ".scout-inbox/phone-notifications-2026-10-01.jsonl": "CANARY-PRIV-6P8N",
    "trading/positions.md": "CANARY-PRIV-7T9P",  # local-only, not private
}

# (subject, files touched) for the three commits after the seed, oldest first.
COMMITS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "private only",
        ("knowledge-base/personal/task-renew-permit.md", "knowledge-base/people/sam.md"),
    ),
    (
        "private and normal",
        ("knowledge-base/topics/billing/billing.md", "knowledge-base/topics/billing/rollout.md"),
    ),
    ("normal only", ("knowledge-base/people/priya.md",)),
)
SEED_SUBJECT = "seed privacy fixture vault"

# Sentinel date → offset in days from today.
_DATE_OFFSETS = {"2000-01-01": -7, "2000-06-30": 3, "2000-12-31": 7}
_TEXT_SUFFIXES = {".md", ".yaml", ".jsonl", ".txt"}


# Telegram targets (stand-ins). A positive chat id is a private chat with the
# owner (owner-only surface); a group or channel id is below zero (shared).
PRIVATE_CHAT_ID = "100200300"
GROUP_CHAT_ID = "-100400500"
STAND_IN_BOT_TOKEN = "000000000:stand-in-token-not-real"


def privacy_secrets(home: Path, chat_id: str = PRIVATE_CHAT_ID, token: str = STAND_IN_BOT_TOKEN) -> Path:
    """Write the Telegram secrets where ``notify_telegram._read_secret`` reads them.

    That is ``<home>/.scout-secrets/telegram-bot-token`` and ``telegram-chat-id``,
    mode 600 (it refuses anything looser). Point ``HOME`` at ``home`` (the
    autouse ``_hermetic_env`` already uses a temp one). Returns the secrets dir.
    """
    secrets = home / ".scout-secrets"
    secrets.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, value in (("telegram-bot-token", token), ("telegram-chat-id", chat_id)):
        path = secrets / name
        path.write_text(value + "\n", encoding="utf-8")
        path.chmod(0o600)
    return secrets


def action_items_rel(today: date | None = None) -> str:
    return f"action-items/action-items-{(today or date.today()).isoformat()}.md"


def git_env() -> dict[str, str]:
    """An environment for git that ignores the user's config."""
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Alex",
        "GIT_AUTHOR_EMAIL": "alex@example.com",
        "GIT_COMMITTER_NAME": "Alex",
        "GIT_COMMITTER_EMAIL": "alex@example.com",
    }


def git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(vault), *args], env=git_env(), check=True, capture_output=True, text=True
    ).stdout


def _rewrite_dates(vault: Path, today: date) -> None:
    for path in vault.rglob("*"):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        new = text
        for sentinel, offset in _DATE_OFFSETS.items():
            new = new.replace(sentinel, (today + timedelta(days=offset)).isoformat())
        if new != text:
            path.write_text(new, encoding="utf-8")


def _commit(vault: Path, subject: str) -> None:
    git(vault, "add", "-A")
    git(vault, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", subject)


def build_privacy_vault(tmp: Path) -> Path:
    """Copy the fixture to ``tmp/vault``, date it to today, and give it a git history."""
    today = date.today()
    vault = tmp / "vault"
    shutil.copytree(FIXTURE, vault)
    _rewrite_dates(vault, today)

    items = vault / action_items_rel(today)
    items.parent.mkdir(parents=True, exist_ok=True)
    items.write_text(
        f"# Action Items — {today.isoformat()}\n\n## To Do\n\n- [ ] 🟡 Review the PROJ-1234 rollout notes with Priya\n",
        encoding="utf-8",
    )
    ontology = vault / "knowledge-base" / "ontology"
    ontology.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ENGINE_SCHEMA, ontology / "schema.yaml")
    (vault / "knowledge_base").symlink_to("knowledge-base")

    git(vault, "init", "-q", "-b", "main")
    _commit(vault, SEED_SUBJECT)
    for n, (subject, rels) in enumerate(COMMITS, 1):
        for rel in rels:
            with (vault / rel).open("a", encoding="utf-8") as fh:
                fh.write(f"\nUpdate {n}.\n")
        _commit(vault, subject)
    return vault
