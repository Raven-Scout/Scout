"""The long-lived Claude token scheduled runs use instead of the interactive login (#267, run-health P6).

An interactive ``claude`` login expires, and every scheduled run then dies on startup.
``claude setup-token`` mints a token that lasts a year; ``scoutctl auth setup-token``
stores it here, and ``scripts/claude-with-retry.sh`` exports it as
``CLAUDE_CODE_OAUTH_TOKEN`` for every run when the variable is not already set.

Storage sits behind ``TokenStore``. On macOS the token is a generic-password item in
the login Keychain (service ``scout-claude-oauth-token``, account = the user). Elsewhere
it is one 0600 file at ``~/.config/scout/claude-oauth-token``. Both live outside the
vault and are never committed (run-health spec §6, Q6 as amended 2026-10-12). A
``~/.config/scout/claude-oauth-token.meta.json`` records when the token was stored,
never the token, so doctor and ``scoutctl health`` can warn before the year runs out:
the CLI offers no way to ask a token's expiry.

The token never goes into an argv: the Keychain item is written through
``security -i``, which reads its command from stdin, and read back with
``find-generic-password -w``.

Nothing here prints, logs or returns the token except ``TokenStore.read``.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import pwd
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

TOKEN_FILENAME = "claude-oauth-token"
META_FILENAME = "claude-oauth-token.meta.json"
ENV_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
# `claude setup-token` says its token is valid for one year.
TOKEN_LIFETIME = timedelta(days=365)
# Inside this window doctor and run health warn (yellow) that the token is about to lapse.
EXPIRY_WARNING = timedelta(days=30)
SETUP_COMMAND = "scoutctl auth setup-token"
KEYCHAIN_SERVICE = "scout-claude-oauth-token"
# `security` exits 44 (errSecItemNotFound) when there is no such item.
_SECURITY_NOT_FOUND = 44

_MIN_TOKEN_LEN = 20
# What `claude setup-token` prints (sk-ant-oat01-…), with room for base64-ish variants. No
# whitespace or shell/`security -i` metacharacters, so a token can be put on a command line.
_TOKEN_CHARS = re.compile(r"[A-Za-z0-9._~+/=-]+")
_ACCOUNT_CHARS = re.compile(r"[A-Za-z0-9._-]+")
_EXPORT_PREFIX = re.compile(rf"^(?:export\s+)?{ENV_VAR}=")


def config_dir(home: Path | None = None) -> Path:
    return (home if home is not None else Path.home()) / ".config" / "scout"


@runtime_checkable
class TokenStore(Protocol):
    """Where the token lives. ``kind`` and ``location`` are safe to print."""

    kind: str
    location: str
    meta_path: Path

    def exists(self) -> bool: ...

    def read(self) -> str | None: ...

    def write(self, token: str) -> None: ...

    def delete(self) -> bool: ...

    def permission_problem(self) -> str | None: ...


class FileTokenStore:
    """The token on one line of a 0600 file in a 0700 directory."""

    kind = "file"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.location = str(path)
        self.meta_path = path.parent / META_FILENAME

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> str | None:
        """The stored token, or None when there is none. Raises OSError when it can't be read."""
        if not self.path.is_file():
            return None
        token = self.path.read_text(encoding="utf-8").strip()
        return token or None

    def write(self, token: str) -> None:
        _write_private(self.path, token + "\n")

    def delete(self) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True

    def permission_problem(self) -> str | None:
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
        except OSError:
            return None
        if mode & 0o077:
            return (
                f"{self.path} is readable by other users (mode {mode:04o}); "
                f"it holds your Claude token. Fix: chmod 600 {self.path}"
            )
        return None


class KeychainTokenStore:
    """The token as a generic-password item in the macOS login Keychain, through ``security``.

    ``security`` is found on PATH (tests put a fake one first). A locked Keychain or a
    refused read raises OSError; ``status`` reports it and the runner falls back to the login.
    """

    kind = "keychain"

    def __init__(self, meta_dir: Path, *, account: str | None = None, security: str = "security") -> None:
        self.account = account or pwd.getpwuid(os.getuid()).pw_name
        self.security = security
        self.location = f"the macOS Keychain (item {KEYCHAIN_SERVICE})"
        self.meta_path = meta_dir / META_FILENAME

    def _run(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        # Output is captured and never shown: `find-generic-password -w` prints the token.
        try:
            return subprocess.run(
                [self.security, *args], input=stdin, capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise OSError(f"couldn't run {self.security}: {e}") from None

    def _item(self) -> list[str]:
        return ["-s", KEYCHAIN_SERVICE, "-a", self.account]

    def _refused(self, what: str, code: int) -> OSError:
        return OSError(f"the Keychain refused to {what} (security exit {code}); it may be locked")

    def exists(self) -> bool:
        rc = self._run(["find-generic-password", *self._item()]).returncode
        if rc in (0, _SECURITY_NOT_FOUND):
            return rc == 0
        raise self._refused("look up the token", rc)

    def read(self) -> str | None:
        proc = self._run(["find-generic-password", *self._item(), "-w"])
        if proc.returncode == _SECURITY_NOT_FOUND:
            return None
        if proc.returncode != 0:
            raise self._refused("read the token", proc.returncode)
        return proc.stdout.strip() or None

    def write(self, token: str) -> None:
        if not _ACCOUNT_CHARS.fullmatch(self.account) or not _TOKEN_CHARS.fullmatch(token):
            raise OSError("can't store this token or account name in the Keychain")
        # Through stdin (`security -i` reads commands there), never argv, which `ps` shows.
        line = f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {self.account} -w {token}\n"
        self._run(["-i"], stdin=line)
        # `security -i` can exit 0 after a failed command, so read it back to know.
        if self.read() != token:
            raise OSError("the Keychain didn't keep the token")

    def delete(self) -> bool:
        rc = self._run(["delete-generic-password", *self._item()]).returncode
        if rc in (0, _SECURITY_NOT_FOUND):
            return rc == 0
        raise self._refused("remove the token", rc)

    def permission_problem(self) -> str | None:
        return None  # the Keychain's own access control applies


def _system() -> str:
    return platform.system()


def default_store(home: Path | None = None) -> TokenStore:
    """The Keychain on macOS, the 0600 file elsewhere. Keep in step with claude-with-retry.sh."""
    if _system() == "Darwin":
        return KeychainTokenStore(config_dir(home))
    return FileTokenStore(config_dir(home) / TOKEN_FILENAME)


def meta_path(store: TokenStore) -> Path:
    return store.meta_path


def _write_private(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path`` as 0600, creating a 0700 parent."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def normalize_token(pasted: str) -> str:
    """The token in what the user pasted: bare, or an ``export CLAUDE_CODE_OAUTH_TOKEN=…`` line.

    Raises ValueError, without echoing the input, when it doesn't look like a token.
    """
    text = _EXPORT_PREFIX.sub("", pasted.strip()).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    if not text:
        raise ValueError("no token given")
    if len(text) < _MIN_TOKEN_LEN or not _TOKEN_CHARS.fullmatch(text):
        raise ValueError("that doesn't look like a token from `claude setup-token` (one line, no spaces)")
    return text


def iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_token(store: TokenStore, token: str, *, now: datetime | None = None) -> None:
    """Store ``token`` and record when it was made (the record never holds the token)."""
    store.write(token)
    meta = {"created_at": iso_z(now or datetime.now(UTC)), "source": "claude setup-token", "store": store.kind}
    _write_private(store.meta_path, json.dumps(meta, indent=2) + "\n")


def clear_token(store: TokenStore) -> bool:
    """Remove the token and its record. True when there was a token to remove."""
    removed = store.delete()
    with contextlib.suppress(FileNotFoundError):
        store.meta_path.unlink()
    return removed


def _read_created_at(path: Path) -> datetime | None:
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        raw = meta["created_at"]
        created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return created if created.tzinfo else created.replace(tzinfo=UTC)


@dataclass(frozen=True)
class AuthStatus:
    """How scheduled runs authenticate. Never carries the token."""

    mode: str  # "token": a stored long-lived token | "login": the interactive Claude login
    store: str
    location: str
    created_at: datetime | None
    expires_at: datetime | None
    days_left: int | None
    problems: tuple[str, ...]

    @property
    def expiring(self) -> bool:
        return self.days_left is not None and self.days_left < EXPIRY_WARNING.days

    def expiry_text(self) -> str | None:
        """'expires in N day(s) (about DATE)' / 'expired about N day(s) ago (DATE)', or None when unknown."""
        if self.days_left is None or self.expires_at is None:
            return None
        if self.days_left >= 0:
            return f"expires in {self.days_left} day(s) (about {self.expires_at:%Y-%m-%d})"
        return f"expired about {-self.days_left} day(s) ago ({self.expires_at:%Y-%m-%d})"

    def describe(self, *, offer_setup: bool = True) -> str:
        if self.mode == "login":
            if not offer_setup:
                return "Scheduled runs use your interactive Claude login."
            return (
                f"Scheduled runs use your interactive Claude login, which expires. For unattended runs: {SETUP_COMMAND}"
            )
        if self.expires_at is None:
            return f"Scheduled runs use a long-lived token from {self.location} (expiry unknown)."
        return (
            f"Scheduled runs use a long-lived token from {self.location}, "
            f"created {self.created_at:%Y-%m-%d}, expires about {self.expires_at:%Y-%m-%d}."
        )

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "store": self.store,
            "location": self.location,
            "created_at": iso_z(self.created_at) if self.created_at else None,
            "expires_at": iso_z(self.expires_at) if self.expires_at else None,
            "days_left": self.days_left,
            "expiring": self.expiring,
            "problems": list(self.problems),
        }


def status(store: TokenStore | None = None, *, now: datetime | None = None) -> AuthStatus:
    store = store if store is not None else default_store()
    now = now or datetime.now(UTC)
    problems: list[str] = []
    try:
        has_token = store.exists()
    except OSError as e:
        has_token = False
        problems.append(f"can't check for a stored token in {store.location}: {e.strerror or e}")
    if has_token:
        try:
            if store.read() is None:
                has_token = False
        except OSError as e:
            problems.append(f"can't read the stored token from {store.location}: {e.strerror or e}")
    if not has_token:
        return AuthStatus("login", store.kind, store.location, None, None, None, tuple(problems))
    perm = store.permission_problem()
    if perm:
        problems.append(perm)
    created = _read_created_at(store.meta_path)
    expires = created + TOKEN_LIFETIME if created else None
    days_left = (expires - now).days if expires else None
    return AuthStatus("token", store.kind, store.location, created, expires, days_left, tuple(problems))


# --- how the last scheduled run signed in --------------------------------------------

SIGN_IN_RECORD = ".claude-sign-in.json"  # in .scout-logs/, written by claude-with-retry.sh
_SOURCE_TEXT = {
    "keychain": "the long-lived token from the macOS Keychain",
    "file": "the long-lived token from ~/.config/scout/claude-oauth-token",
    "environment": "CLAUDE_CODE_OAUTH_TOKEN from its environment",
    "login": "your interactive Claude login",
}


@dataclass(frozen=True)
class LastSignIn:
    at: datetime
    source: str  # keychain | file | environment | login
    token_unreadable: bool  # a stored token existed but the run couldn't read it

    def describe(self) -> str:
        what = _SOURCE_TEXT.get(self.source, self.source)
        text = f"The last scheduled run ({iso_z(self.at)}) signed in with {what}"
        if self.token_unreadable:
            text += ", because it couldn't read the stored token"
        return text + "."


def last_sign_in(vault: Path) -> LastSignIn | None:
    """What ``scripts/claude-with-retry.sh`` recorded for its last run, or None."""
    try:
        raw = json.loads((vault / ".scout-logs" / SIGN_IN_RECORD).read_text(encoding="utf-8"))
        at = datetime.fromtimestamp(int(raw["at"]), tz=UTC)
        source = str(raw["source"])
        unreadable = raw.get("token_unreadable") is True
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None
    return LastSignIn(at, source, unreadable)


# --- `scoutctl auth setup-token` ---------------------------------------------------


def _run_inherited(argv: list[str]) -> int:
    """Run ``argv`` on this terminal: `claude setup-token` signs in through a browser and prints the token."""
    import subprocess

    return subprocess.run(argv, check=False).returncode


def _read_secret(prompt: str) -> str:
    import getpass

    return getpass.getpass(prompt)


def _has_terminal() -> bool:
    return os.isatty(0) and os.isatty(1)


def store_pasted(store: TokenStore, pasted: str, *, say: Callable[[str], None], now: datetime | None = None) -> int:
    """Normalise and store ``pasted``: 0 stored, 1 not a token, 2 couldn't write it."""
    try:
        token = normalize_token(pasted)
    except ValueError as e:
        say(f"Nothing stored: {e}.")
        return 1
    try:
        save_token(store, token, now=now)
    except OSError as e:
        say(f"Nothing stored: couldn't store the token at {store.location}: {e.strerror or e}")
        return 2
    say(status(store, now=now).describe())
    say("Scheduled runs use it from the next run on. Go back to your Claude login with: scoutctl auth clear")
    return 0


def setup_token(
    store: TokenStore,
    *,
    claude_bin: str,
    interactive: bool,
    run: Callable[[list[str]], int],
    read_secret: Callable[[str], str],
    say: Callable[[str], None],
    now: datetime | None = None,
) -> int:
    """Run `claude setup-token`, then store the token the user pastes back. Returns the CLI's exit code."""
    if not interactive:
        say(
            "scoutctl auth setup-token needs a terminal: `claude setup-token` signs you in through your browser. "
            "On a host without one, run `claude setup-token` on a machine with a browser and pipe the token in: "
            "scoutctl auth setup-token --stdin"
        )
        return 2
    say(
        "Scheduled runs use your Claude login, which expires. `claude setup-token` signs you in through "
        "your browser and prints a token that lasts a year."
    )
    say(f"Scout keeps it in {store.location}, outside your vault, for scheduled runs.")
    try:
        code = run([claude_bin, "setup-token"])
    except OSError as e:
        say(f"Couldn't start {claude_bin}: {e.strerror or e}. Pass the claude CLI with --claude-bin.")
        return 2
    if code != 0:
        say(f"claude setup-token failed (exit {code}); nothing stored.")
        return 2
    try:
        pasted = read_secret("Paste the token it printed (input hidden): ")
    except (EOFError, KeyboardInterrupt):
        say("Cancelled; nothing stored.")
        return 1
    return store_pasted(store, pasted, say=say, now=now)
