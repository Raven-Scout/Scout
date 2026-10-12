"""Fakes for ``self_update_run`` tests: a Deps whose every side effect is recorded.

Nothing here touches the network, launchd, cron, desktop notifications or the
real engine pointer. The fake runner pretends to be ``claude`` and the new
engine's ``scoutctl``, and materialises a plugin root under ``tmp_path``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scout.scripts.engine_pointer import EnginePointer
from scout.scripts.self_update import UpdateStatus
from scout.scripts.self_update_run import Deps, Proc

NOW = datetime(2026, 10, 12, 2, 0, tzinfo=UTC)
INTERP = "/engines/0.16.0/.venv/bin/python"
TARGET = "0.17.0"


def pointer(managed_by: str = "install.sh", *, python: str = INTERP, root: str = "/engines/0.16.0") -> EnginePointer:
    return EnginePointer(
        version="0.16.0",
        engine_root=root,
        python=python,
        scoutctl=f"{root}/.venv/bin/scoutctl",
        vault="/vault",
        managed_by=managed_by,
        written_at="2026-10-01T00:00:00Z",
    )


def published(days_ago: float = 2.0, **kw: Any) -> dict:
    return {
        "draft": False,
        "prerelease": False,
        "published_at": (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z"),
        **kw,
    }


class Forbidden(AssertionError):
    pass


def forbidden(name: str) -> Callable[..., Any]:
    def _call(*_a: Any, **_k: Any) -> Any:
        raise Forbidden(f"{name} must not be called")

    return _call


@dataclass
class FakeWorld:
    """Pretends to be `claude` and the new engine's scoutctl."""

    tmp: Path
    target: str = TARGET
    installed_version: str | None = None  # what `claude plugin update` leaves; default target
    overrides: dict[str, Proc | Callable[[list[str], dict | None], Proc]] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)
    envs: list[dict | None] = field(default_factory=list)
    auto_result: dict = field(default_factory=dict)
    doctor_result: dict = field(default_factory=lambda: {"severity": "green", "errors": [], "warnings": []})

    @property
    def new_root(self) -> Path:
        return self.tmp / "cache" / "scout-plugin" / "scout" / self.target

    def _key(self, argv: list[str]) -> str:
        joined = " ".join(argv)
        for key in ("marketplace update", "plugin update", "plugin list", "install-venv.sh", "bootstrap auto"):
            if key in joined:
                return key
        if "bootstrap doctor" in joined:
            return "doctor"
        if argv[-1] == "version":
            return "version"
        return joined

    def __call__(self, argv: list[str], env: dict | None) -> Proc:
        self.calls.append(argv)
        self.envs.append(env)
        key = self._key(argv)
        if key in self.overrides:
            o = self.overrides[key]
            return o(argv, env) if callable(o) else o
        if key == "plugin update":
            meta = self.new_root / ".claude-plugin"
            meta.mkdir(parents=True, exist_ok=True)
            (meta / "plugin.json").write_text(json.dumps({"version": self.installed_version or self.target}))
            return Proc(0, "", "")
        if key == "plugin list":
            return Proc(0, json.dumps([{"id": "scout@scout-plugin", "installPath": str(self.new_root)}]), "")
        if key == "version":
            return Proc(0, f"{self.target}\n", "")
        if key == "bootstrap auto":
            result = {"action": "upgrade", "conflicts": [], "mutated": False, "error": None, **self.auto_result}
            return Proc(0, json.dumps(result) + "\n", "")
        if key == "doctor":
            return Proc(0, json.dumps(self.doctor_result) + "\n", "")
        return Proc(0, "", "")

    def keys(self) -> list[str]:
        return [self._key(a) for a in self.calls]


def make_deps(
    tmp_path: Path,
    *,
    ptr: EnginePointer | None | str = "default",
    config: dict | None = None,
    available: str = TARGET,
    check: Callable[[], UpdateStatus] | None = None,
    release: dict | None | str = "default",
    world: FakeWorld | None = None,
    **kw: Any,
) -> tuple[Deps, FakeWorld, list]:
    vault = tmp_path / "vault"
    (vault / ".scout-logs").mkdir(parents=True, exist_ok=True)
    world = world or FakeWorld(tmp_path)
    notified: list = []
    the_ptr = pointer() if ptr == "default" else ptr
    the_config = config if config is not None else {"auto_update": {"enabled": True, "channel": "stable"}}
    the_release = published() if release == "default" else release
    deps = Deps(
        vault=vault,
        now=lambda: NOW,
        read_pointer=lambda: the_ptr,  # type: ignore[return-value]
        interpreter=INTERP,
        load_config=lambda: the_config,
        check=check or (lambda: UpdateStatus("0.16.0", available, available != "0.16.0")),
        fetch_release=lambda v: the_release,  # type: ignore[return-value]
        run=world,
        lock_busy=lambda: False,
        slot_due_within=lambda w: False,
        in_off_peak=lambda: True,
        doctor_severity=lambda: "green",
        blocking_sidecars=lambda: [],
        claude_bin=lambda: "/bin/claude-fake",
        notify=lambda row, prior: notified.append((row, list(prior))),
        log_name="self-update-2026-10-12.log",
    )
    for k, v in kw.items():
        setattr(deps, k, v)
    return deps, world, notified
