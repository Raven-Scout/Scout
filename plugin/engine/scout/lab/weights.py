"""The owner-side weights cache (spec §6.4): model weights an experiment names, fetched outside every sandbox.

An experiment note may name Hugging Face models, each pinned to a commit (:class:`scout.lab.model.ModelRef`). A
detached run's supervisor (:mod:`scout.lab.supervise`) fetches the ones the cache lacks before the script's gate
opens, into ``~/Library/Caches/Scout/lab-weights/<repo>/<revision>/`` (:func:`cache_root`). The experiment profile
lets the script read the named revisions there and nothing else of the cache, and never write any of it.

**Weights never bring code.** Only files with a suffix in :data:`ALLOWED_SUFFIXES` are fetched; never ``.py``,
``.bin``, ``.pt``, ``.pth``, ``.pkl`` or ``.ckpt`` (pickled weights run code when loaded). A repo with no
``.safetensors`` file is refused, and so is one whose config asks for ``trust_remote_code`` (``auto_map``, or
``"trust_remote_code": true``); the configs are fetched first, so a refused repo leaves nothing in the cache.

**The owner's token never reaches a script.** :func:`read_token` reads the owner's Hugging Face login file; the
supervisor passes it to the HTTP client only. ``HF_TOKEN`` isn't read: it would have to cross the supervisor's inputs
file, which holds nothing secret. :class:`HubHttp` sends it only to ``huggingface.co`` itself, never on a redirect to
another host, and follows redirects only to ``huggingface.co`` and ``hf.co`` hosts, over HTTPS.

**Each file is checked and lands whole.** It streams to a temp name in its revision folder, its size and digest are
checked against the Hub's listing (the LFS ``sha256``, or the git blob id of a small file), and only then is it renamed
to its final name. A fetch that fails partway keeps the files it finished, so the next fetch resumes.

**The cache is capped** at the owner's ``lab.weights_max_bytes`` (0 = no fetching). When full it drops the least
recently used revisions (:func:`evict`, by the last use in ``index.json``) that no ``planned`` or ``running``
experiment names (:func:`in_use`); if that isn't enough, the fetch is refused. A fetch never takes the volume below
the owner's free-disk floor.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any, Protocol

from scout.errors import LabError
from scout.lab.model import ModelRef, model_repo_ok, model_revision_ok

ALLOWED_SUFFIXES = (".safetensors", ".json", ".txt", ".model", ".tiktoken")
WEIGHTS_SUFFIX = ".safetensors"
HUB = "https://huggingface.co"
HUB_HOST = "huggingface.co"
# Where a resolve may redirect: the Hub and its CDNs (cdn-lfs.huggingface.co, cdn-lfs-us-1.hf.co, the Xet bridge).
REDIRECT_DOMAINS = ("huggingface.co", "hf.co")
INDEX_NAME = "index.json"
LOCK_NAME = ".lock"
ROOT_FILES = (INDEX_NAME, LOCK_NAME)
PART_SUFFIX = ".part"
FLOOR_REASON = "free disk would fall below the floor"  # the runner's too (scout.lab.runner.FLOOR_REASON)
FULL_REASON = "the weights cache is full"
OFF_REASON = "fetching model weights is off in the owner's config (lab.weights_max_bytes is 0)"
TIMEOUT_S = 60.0
CHUNK_BYTES = 1 << 20
LISTING_MAX_BYTES = 16 << 20
LOCK_POLL_S = 0.5  # how often a fetch waiting for another one asks whether to stop
CHECK_EVERY_S = 2.0  # how often a download asks whether to stop, and looks at the free disk
CONFIG_PARSE_MAX_BYTES = 8 << 20  # a JSON file this size or smaller is read for remote code (any *config.json is)
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_PART_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._ +=,-]*\Z")
_TOKEN_RE = re.compile(r"[\x21-\x7e]{1,1000}\Z")

_now = time.time  # the clock last use is kept by (tests stand one in)


@dataclass(frozen=True)
class Fetched:
    """One file of a cached model: ``path`` is its place under ``root/<repo>/<revision>/``."""

    repo: str
    revision: str
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class _Remote:
    name: str  # relative to the revision folder
    size: int
    sha256: str | None  # the LFS sha256 the Hub lists, when it does
    blob: str | None  # the git blob id, for a file not in LFS


class Response(Protocol):
    status: int

    def read(self, n: int = -1) -> bytes: ...

    def close(self) -> None: ...


class Http(Protocol):
    """What :func:`fetch` asks of the Hub: a GET with the owner's token (or None), answered with a response whose
    ``status`` is the HTTP status. A failure to reach the Hub raises :class:`OSError`."""

    def get(self, url: str, token: str | None) -> Response: ...


def cache_root(env: Mapping[str, str]) -> Path:
    """``<HOME>/Library/Caches/Scout/lab-weights``, HOME taken from ``env`` (the owner's)."""
    home = env.get("HOME") or os.path.expanduser("~")
    return Path(home) / "Library" / "Caches" / "Scout" / "lab-weights"


def free_bytes(path: Path) -> int | None:
    """Bytes free to this user on the volume holding ``path`` (or its nearest existing folder); None when unreadable."""
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        st = os.statvfs(os.path.realpath(probe))
    except OSError:
        return None
    return st.f_bavail * st.f_frsize


def read_token(env: Mapping[str, str], own_env: Mapping[str, str]) -> str | None:
    """The owner's Hugging Face login, or None: ``$HF_HOME/token`` when ``own_env`` (the supervisor's own
    environment) sets ``HF_HOME``, else ``<HOME>/.cache/huggingface/token`` with HOME from ``env``. ``HF_TOKEN`` is
    never read (the module docstring). A file that isn't one printable token reads as no login."""
    candidates: list[Path] = []
    if own_env.get("HF_HOME"):
        candidates.append(Path(own_env["HF_HOME"]) / "token")
    home = env.get("HOME")
    if home:
        candidates.append(Path(home) / ".cache" / "huggingface" / "token")
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        return text if _TOKEN_RE.match(text) else None
    return None


# -- the real client ------------------------------------------------------------------------------------------------


class RedirectRefused(OSError):
    pass


def _hub_host(url: str) -> str | None:
    """The host of ``url`` when it is an HTTPS URL on the Hub or its CDNs, with no user info or odd port; else None."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        return None
    if parts.port not in (None, 443):
        return None
    if any(host == d or host.endswith("." + d) for d in REDIRECT_DOMAINS):
        return host
    return None


class _HubRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to the Hub or its CDNs, over HTTPS. The token is an unredirected header, so it is
    never carried to the new host, and is put back only when that host is ``huggingface.co`` itself."""

    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> urllib.request.Request | None:
        host = _hub_host(newurl)
        if host is None:
            shown = urllib.parse.urlsplit(newurl).hostname or "?"
            raise RedirectRefused(f"redirect to {shown}, outside the Hub")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and host == HUB_HOST:
            auth = req.unredirected_hdrs.get("Authorization")
            if auth:
                new.add_unredirected_header("Authorization", auth)
        return new


class _Status:
    def __init__(self, status: int) -> None:
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return b""

    def close(self) -> None:
        pass


class _Opened:
    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self.status = int(getattr(resp, "status", 200))

    def read(self, n: int = -1) -> bytes:
        return bytes(self._resp.read(n))

    def close(self) -> None:
        self._resp.close()


class HubHttp:
    """Plain HTTPS to the Hub (``urllib``, the system's certificate checks, no proxy, a timeout on every read)."""

    def __init__(self, timeout_s: float = TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _HubRedirects())

    def get(self, url: str, token: str | None) -> Response:
        if _hub_host(url) != HUB_HOST:
            raise OSError(f"the weights fetch starts only at https://{HUB_HOST}")
        req = urllib.request.Request(url, headers={"User-Agent": "scout-lab-weights"})
        if token:
            req.add_unredirected_header("Authorization", f"Bearer {token}")
        try:
            return _Opened(self._opener.open(req, timeout=self.timeout_s))
        except urllib.error.HTTPError as exc:
            exc.close()
            return _Status(exc.code)


# -- the index of last use ------------------------------------------------------------------------------------------


def _key(repo: str, revision: str) -> str:
    return f"{repo}@{revision}"


def read_index(root: Path) -> dict[str, Any]:
    """``{"revisions": {"<repo>@<rev>": {"last_used", "complete", "files": {name: {size, sha256}}}}}``; empty when
    missing or unreadable."""
    try:
        data = json.loads((root / INDEX_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {"revisions": {}}
    if not isinstance(data, dict) or not isinstance(data.get("revisions"), dict):
        return {"revisions": {}}
    return data


def _write_index(root: Path, index: Mapping[str, Any]) -> None:
    tmp = root / f".{INDEX_NAME}{PART_SUFFIX}"
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(index, fh, sort_keys=True)
    os.replace(tmp, root / INDEX_NAME)


@contextlib.contextmanager
def _locked(root: Path, should_stop: Callable[[], str | None] | None = None) -> Iterator[None]:
    """One fetch at a time per cache (two detached runs may start together). The wait for another fetch asks
    ``should_stop`` as a download does, so a veto, a stop or the deadline still ends it."""
    fd = os.open(root / LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if should_stop is not None and (reason := should_stop()):
                    raise LabError(reason) from None
                time.sleep(LOCK_POLL_S)
        yield
    finally:
        os.close(fd)


# -- what the cache holds -------------------------------------------------------------------------------------------


def _revision_dirs(root: Path) -> list[tuple[str, str, Path]]:
    out: list[tuple[str, str, Path]] = []
    if not root.is_dir():
        return out
    for owner in sorted(root.iterdir()):
        if owner.is_symlink() or not owner.is_dir():
            continue
        for name in sorted(owner.iterdir()):
            if name.is_symlink() or not name.is_dir():
                continue
            for rev in sorted(name.iterdir()):
                if not rev.is_symlink() and rev.is_dir() and _HEX40.match(rev.name):
                    out.append((f"{owner.name}/{name.name}", rev.name, rev))
    return out


def _tree_bytes(folder: Path) -> int:
    """The bytes of every file under ``folder`` (links counted as themselves, never followed)."""
    total = 0
    for top, _dirs, files in os.walk(folder, followlinks=False):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(top, name)).st_size
    return total


def _cache_bytes(root: Path) -> int:
    return sum(_tree_bytes(folder) for _r, _v, folder in _revision_dirs(root))


def _remove_revision(root: Path, repo: str, revision: str) -> None:
    shutil.rmtree(root / repo / revision, ignore_errors=True)
    for folder in (root / repo, (root / repo).parent):
        with contextlib.suppress(OSError):
            folder.rmdir()  # only when empty


def _eviction_plan(root: Path, need: int, in_use: Collection[tuple[str, str]]) -> tuple[list[tuple[str, str]], int]:
    """The least recently used revisions not in ``in_use`` that together free ``need`` bytes, and the bytes they
    hold. A repo is matched case-folded (``Org/M`` and ``org/m`` are one folder on a case-insensitive volume).
    Raises :class:`LabError` ``the weights cache is full`` when even every such revision isn't enough."""
    if need <= 0:
        return [], 0
    protected = {(repo.casefold(), rev) for repo, rev in in_use}
    revisions = read_index(root)["revisions"]
    candidates = []
    for repo, rev, folder in _revision_dirs(root):
        if (repo.casefold(), rev) in protected:
            continue
        entry = revisions.get(_key(repo, rev))
        last = entry.get("last_used", 0.0) if isinstance(entry, dict) else 0.0
        candidates.append((float(last) if isinstance(last, (int, float)) else 0.0, repo, rev, _tree_bytes(folder)))
    candidates.sort()
    chosen, freed = [], 0
    for _last, repo, rev, size in candidates:
        if freed >= need:
            break
        chosen.append((repo, rev))
        freed += size
    if freed < need:
        raise LabError(FULL_REASON)
    return chosen, freed


def evict(root: Path, need: int, in_use: Collection[tuple[str, str]]) -> int:
    """Drop the least recently used revisions not in ``in_use`` until ``need`` bytes are freed; the bytes freed.
    Raises :class:`LabError` ``the weights cache is full`` (dropping nothing) when even every such revision isn't
    enough (:func:`_eviction_plan`)."""
    chosen, freed = _eviction_plan(root, need, in_use)
    _drop(root, chosen)
    return freed


def _drop(root: Path, chosen: Sequence[tuple[str, str]]) -> None:
    if not chosen:
        return
    index = read_index(root)
    for repo, rev in chosen:
        _remove_revision(root, repo, rev)
        index["revisions"].pop(_key(repo, rev), None)
    _write_index(root, index)


def in_use(vault: Path) -> set[tuple[str, str]]:
    """Every ``(repo, revision)`` a ``planned`` or ``running`` experiment note in the vault names."""
    from scout.lab import paths
    from scout.lab.parser import parse_experiment

    out: set[tuple[str, str]] = set()
    lab = paths.lab_dir(vault)
    if not lab.is_dir():
        return out
    for note in sorted(lab.glob("*/experiments/*.md")):
        slug = note.parent.parent.name
        try:
            exp = parse_experiment(slug, note.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, LabError):
            continue
        if exp.status in ("planned", "running"):
            out.update((m.repo, m.revision) for m in exp.models)
    return out


# -- fetching -------------------------------------------------------------------------------------------------------


class _Unusable(LabError):
    """A refusal that leaves nothing of the revision in the cache (remote code, a config that can't be read)."""


def _refused(repo: str, why: object) -> LabError:
    return LabError(f"the Hub refused {repo}: {' '.join(str(why).split())[:200]}")


def _safe_name(name: object) -> bool:
    if not isinstance(name, str) or not name or len(name) > 512 or name.startswith("/"):
        return False
    parts = name.split("/")
    return all(_PART_RE.match(p) and p not in (".", "..") for p in parts)


def _allowed(name: str) -> bool:
    return name.endswith(ALLOWED_SUFFIXES)


def _listing(model: ModelRef, token: str | None, http: Http) -> list[_Remote]:
    url = f"{HUB}/api/models/{model.repo}/revision/{model.revision}?blobs=true"
    resp = _get(model, url, token, http)
    try:
        if resp.status != 200:
            raise _refused(model.repo, resp.status)
        body = _read(model, resp, LISTING_MAX_BYTES + 1)
    finally:
        resp.close()
    if len(body) > LISTING_MAX_BYTES:
        raise _refused(model.repo, "the file listing is too large")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise _refused(model.repo, "the file listing isn't JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("siblings"), list):
        raise _refused(model.repo, "the file listing has no files")
    if "sha" in data and data["sha"] != model.revision:
        raise _refused(model.repo, "revision mismatch")
    out: list[_Remote] = []
    for entry in data["siblings"]:
        name = entry.get("rfilename") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not _allowed(name):
            continue
        if not _safe_name(name):
            raise _refused(model.repo, "unsafe file name")
        size = entry.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise _refused(model.repo, "a file without a size")
        # Every file is fetched against a checksum: an LFS file's sha256, any other file's git blob id. One the
        # listing gives none for is refused, never taken on its size alone.
        lfs = entry.get("lfs")
        if isinstance(lfs, dict):
            sha = lfs.get("sha256")
            if not (isinstance(sha, str) and _HEX64.match(sha)):
                raise _refused(model.repo, f"no checksum for {name}")
            out.append(_Remote(name, size, sha, None))
        else:
            blob = entry.get("blobId")
            if not (isinstance(blob, str) and _HEX40.match(blob)):
                raise _refused(model.repo, f"no checksum for {name}")
            out.append(_Remote(name, size, None, blob))
    if not any(f.name.endswith(WEIGHTS_SUFFIX) for f in out):
        raise LabError(f"no safetensors weights in {model.repo}@{model.revision}")
    # The configs first: a repo that asks for remote code is refused before any weights bytes are fetched.
    return sorted(out, key=lambda f: (not f.name.endswith(".json"), f.name))


def _get(model: ModelRef, url: str, token: str | None, http: Http) -> Response:
    try:
        return http.get(url, token)
    except OSError as exc:
        raise _refused(model.repo, exc) from None


def _read(model: ModelRef, resp: Response, n: int) -> bytes:
    """A read from the Hub; a failure is the Hub's (a local write failure stays an OSError)."""
    try:
        return resp.read(n)
    except OSError as exc:
        raise _refused(model.repo, exc) from None


def _check_json(model: ModelRef, path: Path, name: str, size: int) -> None:
    """Raises :class:`_Unusable` when the JSON file at ``path`` asks for remote code (``auto_map``, or
    ``"trust_remote_code": true``), or is a ``*config.json`` that can't be read, so its remote-code fields can't be
    ruled out. Other JSON files over 8 MiB (a tokenizer's vocabulary) aren't read."""
    is_config = name.endswith("config.json")
    if size > CONFIG_PARSE_MAX_BYTES and not is_config:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        if is_config:
            raise _Unusable(f"the Hub refused {model.repo}: unreadable {name}") from None
        return
    if isinstance(data, dict) and (bool(data.get("auto_map")) or data.get("trust_remote_code") is True):
        raise _Unusable(f"{model.repo} asks for trust_remote_code")


def _digests(path: Path, size: int) -> tuple[str, str]:
    """The sha256 and the git blob id of the file at ``path`` (``size`` bytes)."""
    sha, blob = hashlib.sha256(), hashlib.sha1(b"blob %d\0" % size)
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK_BYTES):
            sha.update(chunk)
            blob.update(chunk)
    return sha.hexdigest(), blob.hexdigest()


def _have(folder: Path, remote: _Remote, files: Mapping[str, Any]) -> str | None:
    """The sha256 of ``remote`` when it is already in ``folder``, whole and right; else None. A file the cache's own
    index recorded (its size and sha256, after that file was checked as it came in) is trusted as recorded; any other
    file is read back and checked against the listing's checksum, as a download is."""
    path = folder / remote.name
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not os.path.isfile(path) or os.path.islink(path) or st.st_size != remote.size:
        return None
    known = files.get(remote.name)
    if isinstance(known, dict) and known.get("size") == remote.size:
        sha = known.get("sha256")
        if isinstance(sha, str) and _HEX64.match(sha):
            return sha if remote.sha256 is None or sha == remote.sha256 else None
    sha, blob = _digests(path, remote.size)
    if remote.sha256 is not None:
        return sha if sha == remote.sha256 else None
    return sha if remote.blob is not None and blob == remote.blob else None


class _Checks:
    """What a download asks every few seconds: a stop (the owner's veto, a deadline) and the free-disk floor."""

    def __init__(self, root: Path, floor: int, should_stop: Callable[[], str | None] | None) -> None:
        self.root, self.floor, self.should_stop = root, floor, should_stop
        self.next = 0.0

    def __call__(self) -> None:
        if time.monotonic() < self.next:
            return
        self.next = time.monotonic() + CHECK_EVERY_S
        if self.should_stop is not None and (reason := self.should_stop()):
            raise LabError(reason)
        if self.floor > 0 and ((free := free_bytes(self.root)) is None or free < self.floor):
            raise LabError(FLOOR_REASON)


def _download(model: ModelRef, remote: _Remote, folder: Path, token: str | None, http: Http, checks: _Checks) -> str:
    """Stream ``remote`` to a temp name beside its final one, check it, rename it; its sha256."""
    final = folder / remote.name
    final.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = final.parent / f".{final.name}{PART_SUFFIX}"
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    url = f"{HUB}/{model.repo}/resolve/{model.revision}/{urllib.parse.quote(remote.name)}"
    sha, blob = hashlib.sha256(), hashlib.sha1(b"blob %d\0" % remote.size)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        got = 0
        resp = _get(model, url, token, http)
        try:
            if resp.status != 200:
                raise _refused(model.repo, resp.status)
            while chunk := _read(model, resp, CHUNK_BYTES):
                got += len(chunk)
                if got > remote.size:
                    raise _refused(model.repo, "size mismatch")
                view = memoryview(chunk)
                while view:
                    view = view[os.write(fd, view) :]
                sha.update(chunk)
                blob.update(chunk)
                checks()
        finally:
            resp.close()
        if got != remote.size:
            raise _refused(model.repo, "size mismatch")
        digest = sha.hexdigest()
        if remote.sha256 is not None and digest != remote.sha256:
            raise _refused(model.repo, "checksum mismatch")
        if remote.sha256 is None and blob.hexdigest() != remote.blob:  # the listing gave one or the other
            raise _refused(model.repo, "checksum mismatch")
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    os.close(fd)
    os.rename(tmp, final)
    return digest


def _fetch_model(
    model: ModelRef,
    remotes: Sequence[_Remote],
    root: Path,
    index: dict[str, Any],
    present: Mapping[str, str],
    fetcher: Callable[[_Remote, Path], str],
) -> list[Fetched]:
    """Every file of ``model``: ``present`` (name → sha256) as it is, the rest through ``fetcher``. The index is
    written after each file, so a fetch cut short resumes without reading back what it finished."""
    folder = root / model.repo / model.revision
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    entry = index["revisions"].setdefault(_key(model.repo, model.revision), {})
    entry.pop("complete", None)
    files = entry.setdefault("files", {})
    out: list[Fetched] = []
    try:
        for remote in remotes:
            sha = present.get(remote.name)
            fetched = sha is None
            if sha is None:
                sha = fetcher(remote, folder)
            files[remote.name] = {"size": remote.size, "sha256": sha}
            if fetched:
                _write_index(root, index)
            if remote.name.endswith(".json"):
                _check_json(model, folder / remote.name, remote.name, remote.size)
            out.append(Fetched(model.repo, model.revision, folder / remote.name, remote.size, sha))
    except _Unusable:
        _remove_revision(root, model.repo, model.revision)
        index["revisions"].pop(_key(model.repo, model.revision), None)
        _write_index(root, index)
        raise
    entry["complete"] = True
    entry["last_used"] = _now()
    _write_index(root, index)
    return out


def _cached(root: Path, index: Mapping[str, Any], model: ModelRef) -> list[Fetched] | None:
    """The files of ``model`` when the index says its revision was fetched whole and they are all still there."""
    entry = index["revisions"].get(_key(model.repo, model.revision))
    if not isinstance(entry, dict) or entry.get("complete") is not True or not isinstance(entry.get("files"), dict):
        return None
    folder = root / model.repo / model.revision
    out: list[Fetched] = []
    for name, meta in entry["files"].items():
        if not _safe_name(name) or not _allowed(name) or not isinstance(meta, dict):
            return None
        size, sha = meta.get("size"), meta.get("sha256")
        path = folder / name
        if not (isinstance(size, int) and isinstance(sha, str) and _HEX64.match(sha)):
            return None
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            return None
        out.append(Fetched(model.repo, model.revision, path, size, sha))
    if not any(f.path.name.endswith(WEIGHTS_SUFFIX) for f in out):
        return None
    return out


def _check_model(model: ModelRef) -> None:
    if not model_repo_ok(model.repo) or not model_revision_ok(model.revision):
        raise LabError(f"{model.repo!r}@{model.revision!r} isn't a model pinned to a commit")


def fetch(
    models: Sequence[ModelRef],
    *,
    root: Path,
    max_bytes: int,
    floor_bytes: int,
    in_use: Collection[tuple[str, str]],
    token: str | None,
    http: Http,
    should_stop: Callable[[], str | None] | None = None,
) -> list[Fetched]:
    """Every file of each of ``models`` in the cache at ``root``, fetching what it lacks (the module docstring).

    ``max_bytes`` caps the cache (0: fetching is off); ``floor_bytes`` is the free space the volume must keep
    (0: none); ``in_use`` are the ``(repo, revision)`` pairs eviction must keep, to which ``models`` are added.
    ``token`` goes to ``http`` only. ``should_stop`` is asked every few seconds of a download; a reason it gives
    stops the fetch with that reason. Raises :class:`LabError` with the reason the experiment is not run."""
    wanted: list[ModelRef] = []
    for model in models:
        _check_model(model)
        if model not in wanted:
            wanted.append(model)
    if not wanted:
        return []
    if max_bytes <= 0:
        raise LabError(OFF_REASON)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    keep = {*in_use, *((m.repo, m.revision) for m in wanted)}
    with _locked(root, should_stop):
        index = read_index(root)
        done: dict[ModelRef, list[Fetched]] = {}
        todo: list[tuple[ModelRef, list[_Remote]]] = []
        for model in wanted:
            cached = _cached(root, index, model)
            if cached is not None:
                done[model] = cached
            else:
                todo.append((model, _listing(model, token, http)))
        need = 0
        present: dict[ModelRef, dict[str, str]] = {}
        for model, remotes in todo:
            entry = index["revisions"].get(_key(model.repo, model.revision))
            files = entry.get("files") if isinstance(entry, dict) else None
            folder = root / model.repo / model.revision
            present[model] = {}
            for r in remotes:
                sha = _have(folder, r, files if isinstance(files, dict) else {})
                if sha is None:
                    need += r.size
                else:
                    present[model][r.name] = sha
        if need > max_bytes:
            raise LabError(FULL_REASON)
        # Plan the eviction, then check the floor with what it would free, and only then drop anything: a fetch
        # the floor refuses leaves the cache as it was.
        chosen, would_free = _eviction_plan(root, _cache_bytes(root) + need - max_bytes, keep)
        free = free_bytes(root)
        if floor_bytes > 0 and (free is None or free + would_free - need < floor_bytes):
            raise LabError(FLOOR_REASON)
        if chosen:
            _drop(root, chosen)
            index = read_index(root)
        checks = _Checks(root, floor_bytes, should_stop)
        for model, remotes in todo:

            def fetcher(remote: _Remote, folder: Path, model: ModelRef = model) -> str:
                return _download(model, remote, folder, token, http, checks)

            done[model] = _fetch_model(model, remotes, root, index, present[model], fetcher)
        for model in done:
            index["revisions"].setdefault(_key(model.repo, model.revision), {})["last_used"] = _now()
        _write_index(root, index)
    return [f for model in wanted for f in sorted(done[model], key=lambda f: str(f.path))]
