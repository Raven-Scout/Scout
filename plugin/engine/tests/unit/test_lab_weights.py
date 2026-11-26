"""The owner-side weights cache (spec §6.4, Task 8): what a fetch takes, refuses, evicts and leaves behind, the
owner's token, and, against the kernel, a script that reads the cache but can't write it.

No test here touches the network: a fake Hub (:class:`FakeHub`) serves a fixture repo's listing and bytes. The
cache lives under the test's HOME, never the real ``~/Library/Caches``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from scout.errors import LabError
from scout.lab import runner, supervise, weights
from scout.lab.model import ModelRef
from scout.sandbox import broker, profile
from tests.unit import lab_runner_helpers as h

kernel = pytest.mark.skipif(not profile.available(), reason="needs sandbox-exec")
REPO = "org/m"
REV = "1" * 40
OTHER_REV = "2" * 40
TOKEN = "hf_owner-login-token-xyz"
WEIGHTS = b"\x08\x00\x00\x00\x00\x00\x00\x00{}      " + b"w" * 4000  # bytes only: never parsed here
CONFIG = json.dumps({"model_type": "gpt2"}).encode()


def _blob(data: bytes) -> str:
    """git's blob id: what the Hub lists as ``blobId`` for a file that isn't in LFS."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"", fail_after: int | None = None) -> None:
        self.status, self._body, self._at, self._fail_after = status, body, 0, fail_after
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        if self._fail_after is not None and self._at >= self._fail_after:
            raise OSError("connection reset")
        end = len(self._body) if n < 0 else self._at + n
        if self._fail_after is not None:
            end = min(end, self._fail_after)
        chunk = self._body[self._at : end]
        self._at += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeHub:
    """A Hub with one repo at one revision: ``files`` name → bytes. LFS files (weights) carry their sha256, the
    rest a git blob id, as the Hub's ``?blobs=true`` listing does."""

    def __init__(
        self,
        files: dict[str, bytes],
        *,
        repo: str = REPO,
        revision: str = REV,
        listing_status: int = 200,
        lfs_sha: dict[str, str] | None = None,
        fail: dict[str, int] | None = None,
    ) -> None:
        self.files, self.repo, self.revision = dict(files), repo, revision
        self.listing_status, self.lfs_sha, self.fail = listing_status, lfs_sha or {}, fail or {}
        self.calls: list[tuple[str, str | None]] = []

    def listing(self) -> dict[str, Any]:
        siblings = []
        for name, data in self.files.items():
            entry: dict[str, Any] = {"rfilename": name, "size": len(data)}
            if name.endswith((".safetensors", ".bin", ".pkl")):
                sha = self.lfs_sha.get(name, hashlib.sha256(data).hexdigest())
                entry["lfs"] = {"sha256": sha, "size": len(data), "pointerSize": 134}
                entry["blobId"] = "f" * 40
            else:
                entry["blobId"] = _blob(data)
            siblings.append(entry)
        return {"id": self.repo, "sha": self.revision, "siblings": siblings}

    def resolves(self) -> list[str]:
        prefix = f"https://huggingface.co/{self.repo}/resolve/{self.revision}/"
        return [urllib.parse.unquote(u[len(prefix) :]) for u, _ in self.calls if u.startswith(prefix)]

    def get(self, url: str, token: str | None) -> FakeResponse:
        self.calls.append((url, token))
        api = f"https://huggingface.co/api/models/{self.repo}/revision/{self.revision}"
        if url.split("?", 1)[0] == api:
            if self.listing_status != 200:
                return FakeResponse(self.listing_status)
            return FakeResponse(200, json.dumps(self.listing()).encode())
        prefix = f"https://huggingface.co/{self.repo}/resolve/{self.revision}/"
        if url.startswith(prefix):
            name = urllib.parse.unquote(url[len(prefix) :])
            if name in self.files:
                return FakeResponse(200, self.files[name], self.fail.get(name))
        return FakeResponse(404)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return weights.cache_root({"HOME": str(tmp_path / "home")})


@pytest.fixture(autouse=True)
def plenty_of_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weights, "free_bytes", lambda _path: 10**15)


def _fetch(root: Path, hub: FakeHub, models: list[ModelRef] | None = None, **kw: Any) -> list[weights.Fetched]:
    args: dict[str, Any] = {"max_bytes": 1 << 30, "floor_bytes": 0, "in_use": set(), "token": TOKEN, "http": hub}
    args.update(kw)
    return weights.fetch(models or [ModelRef(REPO, REV)], root=root, **args)


def _cached_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.name not in weights.ROOT_FILES)


GOOD = {"model.safetensors": WEIGHTS, "config.json": CONFIG, "tokenizer.json": b'{"version": "1.0"}'}


# -- what a fetch takes ---------------------------------------------------------------------------------------------


def test_cache_root_is_under_home(tmp_path: Path) -> None:
    assert weights.cache_root({"HOME": str(tmp_path)}) == tmp_path / "Library" / "Caches" / "Scout" / "lab-weights"
    assert weights.ALLOWED_SUFFIXES == (".safetensors", ".json", ".txt", ".model", ".tiktoken")


def test_fetch_takes_only_allowed_files(root: Path) -> None:
    files = {**GOOD, "modeling.py": b"import os\n", "pytorch_model.bin": b"PICKLE", "x.pkl": b"PICKLE"}
    hub = FakeHub(files)
    got = _fetch(root, hub)
    assert sorted(f.path.name for f in got) == ["config.json", "model.safetensors", "tokenizer.json"]
    assert sorted(hub.resolves()) == ["config.json", "model.safetensors", "tokenizer.json"]
    for f in got:
        assert (f.repo, f.revision) == (REPO, REV)
        assert f.path == root / REPO / REV / f.path.name
        assert f.path.read_bytes() == files[f.path.name]
        assert (f.size, f.sha256) == (len(files[f.path.name]), hashlib.sha256(files[f.path.name]).hexdigest())
    assert _cached_files(root) == sorted(f"{REPO}/{REV}/{n}" for n in GOOD)
    # The token went to the Hub's own host only, every call.
    assert {t for _u, t in hub.calls} == {TOKEN}
    assert all(urllib.parse.urlsplit(u).hostname == "huggingface.co" for u, _ in hub.calls)


def test_fetch_refuses_pickle_only(root: Path) -> None:
    hub = FakeHub({"pytorch_model.bin": b"PICKLE", "config.json": CONFIG})
    with pytest.raises(LabError) as caught:
        _fetch(root, hub)
    assert str(caught.value) == f"no safetensors weights in {REPO}@{REV}"
    assert hub.resolves() == [] and _cached_files(root) == []


@pytest.mark.parametrize(
    "config",
    [
        {"model_type": "x", "auto_map": {"AutoModel": "modeling.Custom"}},
        {"model_type": "x", "trust_remote_code": True},
    ],
)
def test_fetch_refuses_remote_code(root: Path, config: dict[str, Any]) -> None:
    hub = FakeHub({**GOOD, "config.json": json.dumps(config).encode()})
    with pytest.raises(LabError) as caught:
        _fetch(root, hub)
    assert str(caught.value) == f"{REPO} asks for trust_remote_code"
    assert _cached_files(root) == [] and not (root / "org").exists()
    assert "model.safetensors" not in hub.resolves()  # the configs are read first: no weights bytes fetched
    assert REPO + "@" + REV not in json.dumps(weights.read_index(root))


def test_fetch_respects_the_floor(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    need = sum(len(b) for b in GOOD.values())
    monkeypatch.setattr(weights, "free_bytes", lambda _path: 1000 + need - 1)
    hub = FakeHub(GOOD)
    with pytest.raises(LabError) as caught:
        _fetch(root, hub, floor_bytes=1000)
    assert str(caught.value) == "free disk would fall below the floor" == runner.FLOOR_REASON
    assert hub.resolves() == [] and _cached_files(root) == []
    monkeypatch.setattr(weights, "free_bytes", lambda _path: 1000 + need)
    assert len(_fetch(root, hub, floor_bytes=1000)) == 3


def _sized(name_bytes: int) -> dict[str, bytes]:
    return {"model.safetensors": b"w" * name_bytes, "config.json": CONFIG}


def _fill(root: Path, monkeypatch: pytest.MonkeyPatch, revisions: list[str], size: int) -> None:
    """Fetch each of ``revisions`` of ``org/m`` (``size`` bytes of weights each), one second apart in last use."""
    for i, rev in enumerate(revisions):
        monkeypatch.setattr(weights, "_now", lambda i=i: 1000.0 + i)
        _fetch(root, FakeHub(_sized(size), revision=rev), [ModelRef(REPO, rev)])


def test_fetch_evicts_least_recently_used(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    size, one = 10_000, 10_000 + len(CONFIG)
    old, recent, new = "a" * 40, "b" * 40, "c" * 40
    _fill(root, monkeypatch, [old, recent], size)
    monkeypatch.setattr(weights, "_now", lambda: 2000.0)
    _fetch(root, FakeHub(_sized(size), revision=old), [ModelRef(REPO, old)])  # a cache hit refreshes its last use
    got = _fetch(root, FakeHub(_sized(size), revision=new), [ModelRef(REPO, new)], max_bytes=2 * one)
    assert len(got) == 2
    assert (root / REPO / old).is_dir() and (root / REPO / new).is_dir()
    assert not (root / REPO / recent).exists()  # least recently used of the two that were there
    assert f"{REPO}@{recent}" not in weights.read_index(root)["revisions"]


def test_fetch_never_evicts_in_use(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    size, one = 10_000, 10_000 + len(CONFIG)
    old, recent, new = "a" * 40, "b" * 40, "c" * 40
    _fill(root, monkeypatch, [old, recent], size)
    monkeypatch.setattr(weights, "_now", lambda: 3000.0)
    model = [ModelRef(REPO, new)]
    _fetch(root, FakeHub(_sized(size), revision=new), model, max_bytes=2 * one, in_use={(REPO, old)})
    assert (root / REPO / old).is_dir() and not (root / REPO / recent).exists()
    # Both left are named by a planned or running experiment: nothing can go, so the fetch is refused, and nothing
    # is dropped for a fetch that can't happen.
    hub = FakeHub(_sized(size), revision="d" * 40)
    with pytest.raises(LabError, match=r"\Athe weights cache is full\Z"):
        _fetch(root, hub, [ModelRef(REPO, "d" * 40)], max_bytes=2 * one, in_use={(REPO, old), (REPO, new)})
    assert (root / REPO / old).is_dir() and (root / REPO / new).is_dir() and hub.resolves() == []
    with pytest.raises(LabError, match=r"\Athe weights cache is full\Z"):  # bigger than the whole cache
        _fetch(root, FakeHub(_sized(size), revision="d" * 40), [ModelRef(REPO, "d" * 40)], max_bytes=one - 1)


def test_evict_drops_only_what_it_must(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    revs = ["a" * 40, "b" * 40, "c" * 40]
    _fill(root, monkeypatch, revs, 1000)
    freed = weights.evict(root, 1, set())
    assert freed >= 1 and not (root / REPO / revs[0]).exists()
    assert (root / REPO / revs[1]).is_dir() and (root / REPO / revs[2]).is_dir()
    with pytest.raises(LabError, match="the weights cache is full"):
        weights.evict(root, 10**9, set())
    assert (root / REPO / revs[1]).is_dir()  # a refused eviction drops nothing


def test_fetch_is_resumable_and_atomic(root: Path) -> None:
    hub = FakeHub(GOOD, fail={"model.safetensors": 1500})
    with pytest.raises(LabError, match=rf"\Athe Hub refused {REPO}: "):
        _fetch(root, hub)
    folder = root / REPO / REV
    assert not (folder / "model.safetensors").exists()  # an interrupted file never reaches its final name
    assert not [p for p in folder.iterdir() if p.name.startswith(".")]  # nor is its temp left behind
    assert (folder / "config.json").read_bytes() == CONFIG  # what finished is kept for the next try
    again = FakeHub(GOOD)
    got = _fetch(root, again)
    assert again.resolves() == ["model.safetensors"]  # resumed, not restarted
    assert (folder / "model.safetensors").read_bytes() == WEIGHTS and len(got) == 3


def test_a_cached_revision_needs_no_hub(root: Path) -> None:
    first = _fetch(root, FakeHub(GOOD))
    offline = FakeHub({}, listing_status=500)
    assert _fetch(root, offline) == first
    assert offline.calls == []  # a revision is a commit: what is cached whole is still right


@pytest.mark.parametrize("what", ["lfs", "blob"])
def test_fetch_refuses_a_checksum_mismatch(root: Path, what: str) -> None:
    if what == "lfs":
        hub = FakeHub(GOOD, lfs_sha={"model.safetensors": "0" * 64})
    else:
        hub = FakeHub(GOOD)
        listing = hub.listing()
        for entry in listing["siblings"]:
            if entry["rfilename"] == "config.json":
                entry["blobId"] = "0" * 40
        hub.listing = lambda: listing  # type: ignore[method-assign]
    with pytest.raises(LabError) as caught:
        _fetch(root, hub)
    assert str(caught.value) == f"the Hub refused {REPO}: checksum mismatch"
    name = "model.safetensors" if what == "lfs" else "config.json"
    assert not (root / REPO / REV / name).exists()


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_refused_listing_names_its_status(root: Path, status: int) -> None:
    with pytest.raises(LabError) as caught:
        _fetch(root, FakeHub(GOOD, listing_status=status))
    assert str(caught.value) == f"the Hub refused {REPO}: {status}"


def test_a_listing_for_another_revision_is_refused(root: Path) -> None:
    hub = FakeHub(GOOD)
    listing = {**hub.listing(), "sha": OTHER_REV}
    hub.listing = lambda: listing  # type: ignore[method-assign]
    with pytest.raises(LabError, match="the Hub refused org/m: revision mismatch"):
        _fetch(root, hub)


@pytest.mark.parametrize("name", ["../x.json", "/abs.json", "a/../../x.json", "a\\b.json", ".hidden.json"])
def test_a_file_name_that_leaves_the_folder_is_refused(root: Path, name: str) -> None:
    with pytest.raises(LabError, match="the Hub refused org/m: unsafe file name"):
        _fetch(root, FakeHub({**GOOD, name: b"{}"}))
    assert _cached_files(root) == []


def _without(hub: FakeHub, name: str, key: str) -> None:
    """Drop ``key`` (``"lfs.sha256"`` or ``"blobId"``) from ``name``'s entry in ``hub``'s listing."""
    listing = hub.listing()
    for entry in listing["siblings"]:
        if entry["rfilename"] == name:
            if key == "lfs.sha256":
                del entry["lfs"]["sha256"]
            else:
                del entry[key]
    hub.listing = lambda: listing  # type: ignore[method-assign]


@pytest.mark.parametrize(("name", "key"), [("model.safetensors", "lfs.sha256"), ("config.json", "blobId")])
def test_a_file_the_listing_gives_no_checksum_for_is_refused(root: Path, name: str, key: str) -> None:
    hub = FakeHub(GOOD)
    _without(hub, name, key)
    with pytest.raises(LabError) as caught:
        _fetch(root, hub)
    assert str(caught.value) == f"the Hub refused {REPO}: no checksum for {name}"
    assert hub.resolves() == [] and _cached_files(root) == []


@pytest.mark.parametrize("name", ["model.safetensors", "config.json"])
def test_a_same_size_file_already_there_is_checked_not_trusted(root: Path, name: str) -> None:
    folder = root / REPO / REV
    folder.mkdir(parents=True)
    (folder / name).write_bytes(b"X" * len(GOOD[name]))  # the right size, the wrong bytes, not in the index
    hub = FakeHub(GOOD)
    got = {f.path.name: f for f in _fetch(root, hub)}
    assert name in hub.resolves()  # fetched again
    assert (folder / name).read_bytes() == GOOD[name]
    assert got[name].sha256 == hashlib.sha256(GOOD[name]).hexdigest()


def test_a_wait_for_another_fetch_still_hears_a_stop(root: Path) -> None:
    import fcntl

    root.mkdir(parents=True)
    fd = os.open(root / weights.LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # another supervisor's fetch, still going
        hub = FakeHub(GOOD)
        raised: list[BaseException] = []

        def fetch() -> None:
            try:
                _fetch(root, hub, should_stop=lambda: "stopped by the owner")
            except BaseException as e:  # noqa: BLE001 - handed to the test's thread
                raised.append(e)

        # A daemon thread with a bound: a wait that never hears the stop fails here instead of hanging the suite.
        waiter = threading.Thread(target=fetch, daemon=True)
        waiter.start()
        waiter.join(10)
        assert not waiter.is_alive(), "the wait for another fetch never heard the stop"
        [error] = raised
        assert isinstance(error, LabError) and str(error) == "stopped by the owner"
        assert hub.calls == []
    finally:
        os.close(fd)


def test_the_floor_is_checked_before_anything_is_evicted(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    size, one = 10_000, 10_000 + len(CONFIG)
    old, new = "a" * 40, "c" * 40
    _fill(root, monkeypatch, [old], size)
    model = [ModelRef(REPO, new)]
    # What eviction would free doesn't bring the volume back over the floor: refused, and nothing is dropped.
    monkeypatch.setattr(weights, "free_bytes", lambda _path: 999)
    with pytest.raises(LabError, match=r"\Afree disk would fall below the floor\Z"):
        _fetch(root, FakeHub(_sized(size), revision=new), model, max_bytes=one, floor_bytes=1000)
    assert (root / REPO / old).is_dir()
    # With the space the eviction frees counted, the fetch fits.
    monkeypatch.setattr(weights, "free_bytes", lambda _path: 1000)
    assert _fetch(root, FakeHub(_sized(size), revision=new), model, max_bytes=one, floor_bytes=1000)
    assert not (root / REPO / old).exists() and (root / REPO / new).is_dir()


def test_in_use_spellings_match_whatever_their_case(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``Org/M`` and ``org/m`` are one folder on a case-insensitive volume: an in-use revision under either spelling
    is kept."""
    size, one = 10_000, 10_000 + len(CONFIG)
    old, new = "a" * 40, "c" * 40
    _fill(root, monkeypatch, [old], size)
    with pytest.raises(LabError, match=r"\Athe weights cache is full\Z"):
        _fetch(root, FakeHub(_sized(size), revision=new), [ModelRef(REPO, new)], max_bytes=one, in_use={("ORG/M", old)})
    assert (root / REPO / old).is_dir()


def test_a_config_that_wont_parse_is_refused(root: Path) -> None:
    hub = FakeHub({**GOOD, "config.json": b"{not json"})
    with pytest.raises(LabError) as caught:
        _fetch(root, hub)
    assert str(caught.value) == f"the Hub refused {REPO}: unreadable config.json"
    assert _cached_files(root) == [] and "model.safetensors" not in hub.resolves()


def test_nested_and_case_variant_suffixes_are_skipped(root: Path) -> None:
    files = {**GOOD, "sub/modeling.py": b"import os\n", "x.PY": b"import os\n", "x.Safetensors": b"PICKLE?"}
    hub = FakeHub(files)
    got = _fetch(root, hub)
    assert sorted(hub.resolves()) == ["config.json", "model.safetensors", "tokenizer.json"]
    assert sorted(f.path.name for f in got) == ["config.json", "model.safetensors", "tokenizer.json"]
    with pytest.raises(LabError, match="no safetensors weights"):  # a case variant isn't the weights either
        _fetch(root, FakeHub({"x.Safetensors": b"w", "config.json": CONFIG}, revision=OTHER_REV),
               [ModelRef(REPO, OTHER_REV)])  # fmt: skip


def test_fetching_is_off_until_the_owner_sets_a_cap(root: Path) -> None:
    hub = FakeHub(GOOD)
    with pytest.raises(LabError, match="weights_max_bytes"):
        _fetch(root, hub, max_bytes=0)
    assert hub.calls == []


# -- the owner's token ---------------------------------------------------------------------------------------------


def test_the_token_comes_from_the_owners_login_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert weights.read_token({"HOME": str(home)}, {}) is None
    login = home / ".cache" / "huggingface" / "token"
    login.parent.mkdir(parents=True)
    login.write_text(TOKEN + "\n", encoding="utf-8")
    assert weights.read_token({"HOME": str(home)}, {}) == TOKEN
    hf_home = tmp_path / "hf"
    hf_home.mkdir()
    (hf_home / "token").write_text("hf_other\n", encoding="utf-8")
    assert weights.read_token({"HOME": str(home)}, {"HF_HOME": str(hf_home)}) == "hf_other"
    # HF_TOKEN is never read: it would have to cross the supervisor's inputs file to reach it.
    assert weights.read_token({"HOME": str(home), "HF_TOKEN": "hf_env"}, {"HF_TOKEN": "hf_env"}) == TOKEN
    login.write_text("not a\ntoken\n", encoding="utf-8")
    assert weights.read_token({"HOME": str(home)}, {}) is None


def test_token_never_reaches_the_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    env = {**h.make_env(tmp_path), "HF_TOKEN": TOKEN, "SECRET": TOKEN}
    root = weights.cache_root(env)
    script_env = runner.script_env(env, "/tmp/x/.tmp", Path("/tmp/x/m.sock"), "/tmp/x", None, root)
    assert "HF_TOKEN" not in script_env and TOKEN not in script_env.values()
    assert script_env[runner.ENV_WEIGHTS_DIR] == os.path.realpath(root)
    assert set(script_env) <= {
        "PATH", "HOME", "LANG", "TMPDIR", "PYTHONDONTWRITEBYTECODE", "SCOUT_LAB_METER", runner.ENV_EXPERIMENT_DIR,
        runner.ENV_SOURCE_DIR, runner.ENV_WEIGHTS_DIR,
    }  # fmt: skip
    assert runner.ENV_WEIGHTS_DIR not in runner.script_env(env, "/t", Path("/t/s"), "/t", None, None)


# -- the real client's rules ----------------------------------------------------------------------------------------


def _redirect(url: str, to: str, token: str | None = TOKEN) -> urllib.request.Request | None:
    req = urllib.request.Request(url)
    if token:
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    return weights._HubRedirects().redirect_request(req, None, 302, "Found", Message(), to)


@pytest.mark.parametrize(
    ("to", "keeps_token"),
    [
        ("https://huggingface.co/api/resolve-cache/models/org/m/x/config.json", True),
        ("https://cdn-lfs.huggingface.co/repos/ab/cd/x", False),
        ("https://cdn-lfs-us-1.hf.co/repos/ab/cd/x", False),
        ("https://cas-bridge.xethub.hf.co/xet-bridge-us/x", False),
    ],
)
def test_redirects_stay_on_the_hub_and_the_token_stays_on_its_host(to: str, keeps_token: bool) -> None:
    new = _redirect(f"https://huggingface.co/{REPO}/resolve/{REV}/config.json", to)
    assert new is not None and new.full_url == to
    headers = {k.lower(): v for k, v in [*new.header_items()]}
    assert ("authorization" in headers) is keeps_token
    if keeps_token:
        assert headers["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    "to",
    [
        "http://cdn-lfs.huggingface.co/x",  # not HTTPS
        "https://evil.example/x",
        "https://huggingface.co.evil.example/x",
        "https://evilhf.co/x",
        "https://user@huggingface.co/x",
    ],
)
def test_a_redirect_off_the_hub_is_refused(to: str) -> None:
    with pytest.raises(OSError, match="redirect"):
        _redirect(f"https://huggingface.co/{REPO}/resolve/{REV}/config.json", to)


def test_the_client_only_starts_at_the_hub() -> None:
    client = weights.HubHttp()
    for url in ("http://huggingface.co/x", "https://cdn-lfs.huggingface.co/x", "https://example.com/x"):
        with pytest.raises(OSError, match="huggingface.co"):
            client.get(url, TOKEN)


# -- the vault's experiments that hold revisions -------------------------------------------------------------------


def test_in_use_is_every_planned_or_running_experiments_models(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)  # E2 running
    note = h.exp_dir(vault).with_suffix(".md")
    models = f'models:\n  - repo: {REPO}\n    revision: "{REV}"\n'
    note.write_text(note.read_text(encoding="utf-8").replace("visibility: normal\n", f"visibility: normal\n{models}"))
    assert weights.in_use(vault) == {(REPO, REV)}
    note.write_text(note.read_text(encoding="utf-8").replace("status: running", "status: done", 1))
    assert weights.in_use(vault) == set()
    assert weights.in_use(tmp_path / "no-vault") == set()


# -- the supervisor fetches before the gate ------------------------------------------------------------------------


def _sup_inputs(vault: Path, env: dict[str, str], models: list[ModelRef]) -> supervise.Inputs:
    limits = broker.LabLimits(max_total_bytes=64 << 20, disk_free_floor_bytes=0)
    return supervise.Inputs(
        vault=vault, slug=h.SLUG, eid=h.EID, argv=["/bin/sh", "-c", "true"], commit=None, cap_usd=1.0,
        deadline_s=3600, limits={k: int(getattr(limits, k)) for k in supervise.LIMIT_KEYS}, env=env,
        claude_argv=["claude"], models=models, weights_max_bytes=1 << 30, python=None,
    )  # fmt: skip


def test_the_supervisor_fetches_with_the_owners_token_and_hands_the_runner_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HF_HOME", raising=False)
    vault = h.make_vault(tmp_path)
    env = h.make_env(tmp_path)
    login = Path(env["HOME"]) / ".cache" / "huggingface" / "token"
    login.parent.mkdir(parents=True)
    login.write_text(TOKEN, encoding="utf-8")
    hub = FakeHub(GOOD)
    monkeypatch.setattr(weights, "HubHttp", lambda: hub)
    seen: dict[str, Any] = {}

    def fake_run(_v: Path, _s: str, _e: str, argv: list[str], **kw: Any) -> runner.RunOutcome:
        seen.update(kw, argv=argv)
        return runner.RunOutcome(0, "", "", 0.0, False)

    monkeypatch.setattr(runner, "run_experiment", fake_run)
    inputs = _sup_inputs(vault, env, [ModelRef(REPO, REV)])
    assert TOKEN not in json.dumps(inputs.to_dict())
    outcome = supervise.supervise(inputs)
    assert outcome["status"] == "ran", outcome
    assert {t for _u, t in hub.calls} == {TOKEN}
    assert seen["weights_root"] == weights.cache_root(env)
    assert sorted(f.path.name for f in seen["weights"]) == ["config.json", "model.safetensors", "tokenizer.json"]
    assert TOKEN not in json.dumps({k: str(v) for k, v in seen.items()})


def test_a_refused_fetch_ends_the_experiment_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = h.make_vault(tmp_path)
    monkeypatch.setattr(weights, "HubHttp", lambda: FakeHub({"pytorch_model.bin": b"PICKLE"}))

    def never(*_a: Any, **_k: Any) -> runner.RunOutcome:
        raise AssertionError("the script ran")

    monkeypatch.setattr(runner, "run_experiment", never)
    outcome = supervise.supervise(_sup_inputs(vault, h.make_env(tmp_path), [ModelRef(REPO, REV)]))
    assert outcome["status"] == "not run" and outcome["reason"] == f"no safetensors weights in {REPO}@{REV}"
    reply = broker.experiment_status(vault, h.SLUG, h.EID, h.make_env(tmp_path), datetime.now(UTC))
    assert reply.stderr == f"experiment not run: no safetensors weights in {REPO}@{REV}\n"


def test_the_inputs_carry_the_models_and_the_owners_python(tmp_path: Path) -> None:
    vault = h.make_vault(tmp_path)
    inputs = _sup_inputs(vault, h.make_env(tmp_path), [ModelRef(REPO, REV)])
    back = supervise.Inputs.from_dict(json.loads(json.dumps(inputs.to_dict())))
    assert back == inputs and back.models == [ModelRef(REPO, REV)]
    bad = {**inputs.to_dict(), "models": [{"repo": "../x", "revision": REV}]}
    with pytest.raises(LabError, match="inputs aren't usable"):
        supervise.Inputs.from_dict(bad)


# -- the owner's Python ---------------------------------------------------------------------------------------------


def test_python3_runs_the_owners_interpreter(tmp_path: Path) -> None:
    venv = tmp_path / "home" / "torch-venv"
    py = venv / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.symlink_to(os.path.realpath(sys.executable))
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    assert runner.owner_python_argv(["python3", "/s/run.py", "a"], str(py)) == [str(py), "/s/run.py", "a"]
    assert runner.owner_python_argv(["python3", "/s/run.py"], None) == ["python3", "/s/run.py"]
    assert runner.owner_python_argv(["bash", "/s/run.sh"], str(py)) == ["bash", "/s/run.sh"]
    env = {**h.make_env(tmp_path)}
    reads = runner._allow_reads(tmp_path / "vault", env, [], python=str(py))
    # The venv's folder (its site-packages) and the interpreter it runs, both to read.
    assert Path(os.path.realpath(venv)) in reads
    homes = runner.sandbox.homes(env)
    assert Path(runner._interpreter_root(os.path.realpath(py), homes)) in reads


# -- against the kernel ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["home", "not-bin"])
def test_a_pyvenv_cfg_only_makes_a_venv_of_a_bin_folder_below_home(tmp_path: Path, layout: str) -> None:
    env = h.make_env(tmp_path)
    home = Path(env["HOME"])
    if layout == "home":  # HOME/pyvenv.cfg with HOME/bin/python3: HOME itself must never come back
        venv, py = home, home / "bin" / "python3"
    else:  # HOME/tools/pyvenv.cfg with HOME/tools/libexec/python3: not a venv's interpreter
        venv, py = home / "tools", home / "tools" / "libexec" / "python3"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.symlink_to(os.path.realpath(sys.executable))
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    reads = runner._allow_reads(tmp_path / "vault", env, [], python=str(py))
    assert Path(os.path.realpath(venv)) not in reads


@pytest.fixture
def cached(tmp_path: Path) -> Iterator[tuple[Path, list[weights.Fetched]]]:
    env = h.make_env(tmp_path)
    root = weights.cache_root(env)
    got = _fetch(root, FakeHub(GOOD))
    other = _fetch(root, FakeHub(GOOD, revision=OTHER_REV), [ModelRef(REPO, OTHER_REV)])
    assert other
    yield root, got


def _run_with_weights(
    vault: Path, tmp_path: Path, script: str, root: Path, fetched: list[weights.Fetched]
) -> runner.RunOutcome:
    return runner.run_experiment(
        vault, h.SLUG, h.EID, ["/bin/sh", "-c", script], cap_usd=1.0, timeout_s=60, env=h.make_env(tmp_path),
        claude_argv=(str(h.FAKE), str(tmp_path / "calls.log")), weights=fetched, weights_root=root,
        disk_free_floor_bytes=0,
    )  # fmt: skip


@kernel
def test_kernel_script_reads_but_cannot_write_weights(
    tmp_path: Path, cached: tuple[Path, list[weights.Fetched]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    root, fetched = cached
    vault = h.make_vault(tmp_path)
    here = f'"$SCOUT_LAB_WEIGHTS_DIR/{REPO}/{REV}'
    read = _run_with_weights(vault, tmp_path, f'cat {here}/config.json"', root, fetched)
    assert read.exit == 0 and "gpt2" in read.stdout_tail, read.stderr_tail
    for target in (f'{here}/new.bin"', f'{here}/config.json"'):
        write = _run_with_weights(vault, tmp_path, f"echo x > {target}", root, fetched)
        assert write.exit != 0 and "Operation not permitted" in write.stderr_tail, (target, write.stderr_tail)
    assert not (root / REPO / REV / "new.bin").exists() and (root / REPO / REV / "config.json").read_bytes() == CONFIG
    rm = _run_with_weights(vault, tmp_path, f'rm -f {here}/config.json"', root, fetched)
    assert rm.exit != 0 and (root / REPO / REV / "config.json").exists()
    other = _run_with_weights(
        vault, tmp_path, f'cat "$SCOUT_LAB_WEIGHTS_DIR/{REPO}/{OTHER_REV}/config.json"', root, fetched
    )
    assert other.exit != 0 and "gpt2" not in other.stdout_tail  # another revision of the same repo isn't named
    env_out = _run_with_weights(vault, tmp_path, "/usr/bin/env", root, fetched)
    assert env_out.exit == 0 and "HF_TOKEN" not in env_out.stdout_tail and TOKEN not in env_out.stdout_tail
    log = sorted(h.exp_dir(vault).glob("run-*.log"))[-1].read_text(encoding="utf-8")
    for f in fetched:
        assert f"{f.repo}@{f.revision} {f.path.name} sha256:{f.sha256}" in log, log
