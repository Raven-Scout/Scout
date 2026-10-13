#!/usr/bin/env python3
"""Headless install + self-update end to end, inside the disposable container (README.md here).

`run.sh` starts this in the container with /repo (this checkout, read-only) and /work (scratch)
mounted. /work/src.git holds two branches: `e2e-from` (the release a user has installed) and
`e2e-target` (what `stable` moves to next: a tag, a release candidate, or a local build).

Each scenario gets its own HOME: it installs `e2e-from` with that release's own install.sh, a
stub `claude` and cron, seeds the vault with the eval fixture's stand-ins, then drives
`scoutctl self-update run` and checks what run-health spec §7 and §11 promise. Standard
library only: it runs on the image's system Python, never the engine's.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

HARNESS = Path(__file__).resolve().parent
REPO = HARNESS.parents[2]
WORK = Path(os.environ.get("SCOUT_E2E_WORK", "/work"))
SRC = WORK / "src.git"
REMOTE = WORK / "remote.git"
BUILD = WORK / "build"
EVAL_VAULT = REPO / "plugin" / "engine" / "tests" / "eval" / "vault"
PLUGIN_ID = "scout@scout-plugin"
SCENARIOS = ("happy", "rollback", "sidecars")

# Issues this harness is expected to surface on Linux. A check tied to one reports KNOWN,
# not FAIL, so the run stays green until the fix lands (and then reports PASS).
KNOWN_VAULT_NOT_GIT = "Raven-Scout/Scout#433"
KNOWN_BSD_DATE = "Raven-Scout/Scout#434"
# bootstrap_doctor runs `launchctl list` on every POSIX host, so a Linux install is
# always yellow ("launchctl unavailable") and install.sh exits 1 after a good install.
KNOWN_LAUNCHCTL_ON_LINUX = "Raven-Scout/Scout#449"


# --- reporting ------------------------------------------------------------------------------


class Report:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict] = []
        self.scenario = "setup"

    def check(self, name: str, ok: bool, detail: str = "", *, known: str | None = None) -> bool:
        status = "PASS" if ok else ("KNOWN" if known else "FAIL")
        row = {"scenario": self.scenario, "check": name, "status": status, "detail": detail}
        if known:
            row["issue"] = known
        self.rows.append(row)
        mark = {"PASS": "ok  ", "FAIL": "FAIL", "KNOWN": "KNWN"}[status]
        tail = f" [{known}]" if known and not ok else ""
        print(f"  {mark} {name}{tail}" + (f" — {detail}" if detail and not ok else ""), flush=True)
        self.write()
        return ok

    def info(self, name: str, detail: str) -> None:
        self.rows.append({"scenario": self.scenario, "check": name, "status": "INFO", "detail": detail})
        print(f"  info {name}: {detail}", flush=True)
        self.write()

    def write(self) -> None:
        self.path.write_text(json.dumps(self.rows, indent=2) + "\n", encoding="utf-8")

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.rows if r["status"] == "FAIL"]


# --- small helpers --------------------------------------------------------------------------


def semver(v: str) -> tuple[int, int, int]:
    core = v.split("+")[0].split("-")[0].split(".")[:3]
    nums = [int(p) for p in core] + [0, 0, 0]
    return nums[0], nums[1], nums[2]


def git(*args: str, cwd: Path | None = None, git_dir: Path | None = None) -> str:
    argv = ["git", *([f"--git-dir={git_dir}"] if git_dir else []), *args]
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def ref_version(ref: str) -> str:
    return json.loads(git("show", f"{ref}:plugin/.claude-plugin/plugin.json", git_dir=REMOTE))["version"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def zone_where(lo: tuple[int, int], hi: tuple[int, int]) -> str:
    """An IANA zone whose local time of day is inside [lo, hi] right now.

    The updater reads the host's zone (``TZ``) for its off-peak window and the slot lookahead,
    so picking the zone pins those gates without faking the clock.
    """
    now = datetime.now(UTC)
    # Whole-hour Etc/ zones first (readable in logs), then the rest for :30/:45 offsets.
    zones = sorted(n for n in available_timezones() if "/" in n and not n.startswith(("posix/", "right/")))
    for name in [n for n in zones if n.startswith("Etc/GMT")] + zones:
        local = now.astimezone(ZoneInfo(name))
        if lo <= (local.hour, local.minute) <= hi:
            return name
    raise SystemExit(f"no zone has a local time between {lo} and {hi}")


def load_render_dates():
    """The eval fixture builder's date-macro renderer (stdlib only), loaded by path."""
    spec = importlib.util.spec_from_file_location(
        "e2e_eval_dates", REPO / "plugin" / "engine" / "tests" / "eval" / "harness" / "dates.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render_dates


# --- the release fixture ----------------------------------------------------------------------


class Releases:
    """The local stand-in for github.com/Raven-Scout/Scout: branches in REMOTE, release JSON per host."""

    def __init__(self, as_version: str | None) -> None:
        if REMOTE.exists():
            shutil.rmtree(REMOTE)
        git("clone", "-q", "--bare", str(SRC), str(REMOTE))
        self.from_version = ref_version("e2e-from")
        target_version = ref_version("e2e-target")
        stamp = as_version
        if stamp is None and semver(target_version) <= semver(self.from_version):
            f = semver(self.from_version)
            stamp = f"{f[0]}.{f[1]}.{f[2] + 1}"
        if BUILD.exists():
            shutil.rmtree(BUILD)
        git("clone", "-q", str(REMOTE), str(BUILD))
        git("checkout", "-q", "--detach", "origin/e2e-target", cwd=BUILD)
        git("config", "user.name", "Scout e2e", cwd=BUILD)
        git("config", "user.email", "e2e@example.com", cwd=BUILD)
        if stamp:
            # A local build at (or below) the installed version: stamp a newer one, the way
            # release.sh prepare does, so the updater sees a release to take.
            self._stamp(stamp)
        self.version = stamp or target_version
        self.stamped = bool(stamp)
        self.good = self._push("rel-good")
        self.broken = self._broken()
        # The release after the target, for "a fix ships as the next patch".
        t = semver(self.version)
        self.next_version = f"{t[0]}.{t[1]}.{t[2] + 1}"
        git("checkout", "-q", "--detach", self.good, cwd=BUILD)
        self._stamp(self.next_version)
        self.next = self._push("rel-next")
        git("checkout", "-q", "--detach", self.good, cwd=BUILD)

    def _stamp(self, version: str) -> None:
        subprocess.run(
            [sys.executable, "plugin/engine/scout/scripts/versioning.py", "--repo-root", ".", "set", version],
            cwd=BUILD,
            check=True,
            capture_output=True,
        )
        git("commit", "-q", "-am", f"e2e: stamp {version}", cwd=BUILD)

    def _push(self, branch: str) -> str:
        git("push", "-q", "-f", "origin", f"HEAD:refs/heads/{branch}", cwd=BUILD)
        return git("rev-parse", "HEAD", cwd=BUILD)

    def _broken(self) -> str:
        """The good release with an install-venv.sh that fails: the induced pre-vault failure."""
        git("checkout", "-q", "--detach", self.good, cwd=BUILD)
        script = BUILD / "plugin" / "scripts" / "install-venv.sh"
        text = script.read_text(encoding="utf-8")
        marker = "set -euo pipefail\n"
        assert marker in text, "install-venv.sh has no `set -euo pipefail` line to anchor the fault"
        text = text.replace(marker, marker + 'echo "e2e: induced install-venv failure" >&2\nexit 1\n', 1)
        script.write_text(text, encoding="utf-8")
        git("commit", "-q", "-am", "e2e: break install-venv.sh", cwd=BUILD)
        sha = self._push("rel-broken")
        git("checkout", "-q", "--detach", self.good, cwd=BUILD)
        return sha

    def with_phase_change(self, line: str, replacement: str) -> str:
        """The good release with one phase line changed: an upstream edit a local edit can conflict with."""
        git("checkout", "-q", "--detach", self.good, cwd=BUILD)
        hits = [p for p in (BUILD / "plugin" / "phases").rglob("*.md") if line in p.read_text(encoding="utf-8")]
        assert len(hits) == 1, f"line is in {len(hits)} phase files"
        hits[0].write_text(hits[0].read_text(encoding="utf-8").replace(line, replacement, 1), encoding="utf-8")
        git("commit", "-q", "-am", "e2e: change a phase line upstream", cwd=BUILD)
        sha = self._push("rel-sidecars")
        git("checkout", "-q", "--detach", self.good, cwd=BUILD)
        return sha

    def phase_lines(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in (BUILD / "plugin" / "phases").rglob("*.md"):
            for raw in p.read_text(encoding="utf-8").splitlines():
                counts[raw] = counts.get(raw, 0) + 1
        return counts

    @staticmethod
    def move_stable(sha: str) -> None:
        """What `release.sh finalize` does to `stable` (and the marketplace's default branch)."""
        for branch in ("stable", "main"):
            git("update-ref", f"refs/heads/{branch}", sha, git_dir=REMOTE)
        git("symbolic-ref", "HEAD", "refs/heads/main", git_dir=REMOTE)


# --- one disposable host --------------------------------------------------------------------


class Host:
    def __init__(self, name: str, report: Report, releases: Releases, quiet_tz: str) -> None:
        self.name = name
        self.report = report
        self.releases = releases
        self.home = WORK / "homes" / name
        self.state = WORK / "state" / name
        for d in (self.home, self.state):
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
        (self.state / "releases").mkdir()
        self.vault = self.home / "Scout"
        self.tz = quiet_tz
        self.log = (WORK / "logs" / f"{name}.log").open("w", encoding="utf-8")

    # -- processes

    def localtime(self, zone: str) -> str:
        """A /etc/localtime stand-in for ``zone``.

        The updater reads the host zone two ways: the slot lookahead takes ``TZ`` first, the
        off-peak window takes the vault zone (the /etc/localtime link, or ``SCOUT_LOCALTIME``).
        Both are set, so both gates see the same clock.
        """
        link = self.state / f"localtime-{zone.replace('/', '_')}"
        if not link.is_symlink():
            link.symlink_to(Path("/usr/share/zoneinfo") / zone)
        return str(link)

    def env(self, tz: str | None = None) -> dict[str, str]:
        zone = tz or self.tz
        env = {
            "HOME": str(self.home),
            "USER": "scout",
            "LOGNAME": "scout",
            "SHELL": "/bin/bash",
            "PATH": f"{HARNESS / 'bin'}:{self.home / '.local' / 'bin'}:/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": str(HARNESS / "pysite"),
            "SCOUT_E2E_REMOTE": str(REMOTE),
            "SCOUT_E2E_STATE": str(self.state),
            "TZ": zone,
            "SCOUT_LOCALTIME": self.localtime(zone),
            "LANG": "C.UTF-8",
            "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(WORK / "uv-cache")),
            "UV_PYTHON_INSTALL_DIR": os.environ.get("UV_PYTHON_INSTALL_DIR", str(WORK / "uv-python")),
        }
        return env

    def run(self, argv: list[str], *, timeout: int = 1200, tz: str | None = None) -> subprocess.CompletedProcess:
        self.log.write(f"\n$ {' '.join(argv)}   (TZ={tz or self.tz})\n")
        self.log.flush()
        proc = subprocess.run(argv, env=self.env(tz), cwd=self.home, capture_output=True, text=True, timeout=timeout)
        self.log.write(proc.stdout + proc.stderr + f"[exit {proc.returncode}]\n")
        self.log.flush()
        return proc

    def scoutctl(self, *args: str, tz: str | None = None) -> subprocess.CompletedProcess:
        return self.run([str(self.home / ".local" / "bin" / "scoutctl"), *args], tz=tz)

    def self_update(self, *flags: str, tz: str | None = None) -> dict:
        proc = self.scoutctl("self-update", "run", "--json", *flags, tz=tz)
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            return {"row": None, "reason": f"<no JSON: exit {proc.returncode}: {proc.stderr.strip()[-300:]}>"}

    def health(self) -> tuple[int, dict]:
        proc = self.scoutctl("health", "--json")
        try:
            return proc.returncode, json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.returncode, {}

    def doctor_severity(self) -> str:
        proc = self.scoutctl("bootstrap", "doctor", "--json")
        try:
            return str(json.loads(proc.stdout).get("severity"))
        except json.JSONDecodeError:
            return f"<unreadable: exit {proc.returncode}>"

    # -- state

    def pointer(self) -> dict:
        path = self.home / ".local" / "state" / "scout" / "engine.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def crontab(self) -> str:
        return subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout

    def outcomes(self) -> list[dict]:
        path = self.vault / ".scout-logs" / "update-outcomes.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def calls(self, name: str) -> list[dict]:
        path = self.state / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def pushes(self) -> list[dict]:
        return self.calls("notify.jsonl")

    def cache_root(self, version: str) -> Path:
        return self.home / ".claude" / "plugins" / "cache" / "scout-plugin" / "scout" / version

    def config_value(self, *keys: str) -> object:
        """A scout-config.yaml value, read with the live engine's interpreter (it has PyYAML)."""
        python = self.pointer().get("python")
        if not python:
            return None
        code = (
            "import json, sys, yaml\n"
            "node = yaml.safe_load(open(sys.argv[1], encoding='utf-8')) or {}\n"
            "for key in sys.argv[2:]:\n"
            "    node = node.get(key) if isinstance(node, dict) else None\n"
            "print(json.dumps(node))\n"
        )
        proc = self.run([python, "-c", code, str(self.vault / "scout-config.yaml"), *keys])
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None

    def release(self, version: str, *, hours_ago: float | None, prerelease: bool = False) -> None:
        """Publish (or with ``hours_ago=None`` withdraw) GitHub release v<version>."""
        path = self.state / "releases" / f"v{version}.json"
        if hours_ago is None:
            path.unlink(missing_ok=True)
            return
        published = (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {"tag_name": f"v{version}", "draft": False, "prerelease": prerelease, "published_at": published}
        path.write_text(json.dumps(body), encoding="utf-8")

    def flag(self, name: str, on: bool) -> None:
        path = self.state / name
        path.touch() if on else path.unlink(missing_ok=True)

    # -- install

    def install(self) -> bool:
        """Install e2e-from the supported headless way: that release's install.sh, cron, no desktop."""
        r = self.report
        subprocess.run(["crontab", "-r"], capture_output=True)  # one user, scenarios run in turn
        Releases.move_stable(git("rev-parse", "e2e-from", git_dir=REMOTE))
        src = WORK / "src-from"
        if not src.exists():
            src.mkdir()
            archive = subprocess.run(
                ["git", f"--git-dir={REMOTE}", "archive", "e2e-from", "install.sh"], check=True, capture_output=True
            ).stdout
            subprocess.run(["tar", "-x", "-C", str(src)], input=archive, check=True)
        proc = self.run(
            [
                "bash",
                str(src / "install.sh"),
                "--yes",
                "--name",
                "Alex",
                "--email",
                "alex@example.com",
                "--no-first-run",
                "--auto-update",
            ],
            timeout=1800,
        )
        # `scoutctl setup` exits with `bootstrap auto`'s code: 1 means installed, doctor yellow.
        ok = r.check(
            "install.sh installs (exit 0, or 1 = doctor yellow)",
            proc.returncode in (0, 1),
            (proc.stderr or proc.stdout).strip()[-400:],
        )
        doctor = self.doctor_severity()
        r.check(
            "a fresh headless Linux install's doctor is green",
            doctor == "green",
            f"doctor {doctor}, install.sh exit {proc.returncode}",
            known=KNOWN_LAUNCHCTL_ON_LINUX,
        )
        ptr = self.pointer()
        ok &= r.check(
            "pointer: install.sh engine at the installed release",
            ptr.get("managed_by") == "install.sh" and ptr.get("version") == self.releases.from_version,
            json.dumps({k: ptr.get(k) for k in ("managed_by", "version", "engine_root")}),
        )
        enabled = self.config_value("auto_update", "enabled")
        ok &= r.check("auto_update.enabled is on after a headless install", enabled is True, repr(enabled))
        tab = self.crontab()
        ok &= r.check("cron holds Scout's jobs (no launchd, no desktop)", "scout" in tab.lower(), tab.strip()[:300])
        return ok

    def seed(self, render_dates) -> dict[str, str]:
        """Overlay the eval fixture's stand-in content and point notify.command at the sink."""
        today = date.today()
        hashes = {}
        for path in sorted(p for p in EVAL_VAULT.rglob("*") if p.is_file()):
            rel = render_dates(path.relative_to(EVAL_VAULT).as_posix(), today)
            dest = self.vault / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(render_dates(path.read_text(encoding="utf-8"), today), encoding="utf-8")
            hashes[rel] = sha256(dest)
        cfg = self.vault / "scout-config.yaml"
        text = cfg.read_text(encoding="utf-8")
        assert not re.search(r"^notify:", text, re.M), "the fresh vault already has a notify block"
        cfg.write_text(
            text.rstrip("\n") + f"\nnotify:\n  command: {HARNESS / 'bin' / 'notify-sink'}\n", encoding="utf-8"
        )
        if (self.vault / ".git").exists():
            self.run(["git", "-C", str(self.vault), "add", "-A"])
            self.run(["git", "-C", str(self.vault), "commit", "-qm", "e2e: fixture content"])
        return hashes

    def known_linux_issues(self) -> None:
        r = self.report
        r.check(
            "the fresh vault is a git repo",
            (self.vault / ".git").is_dir(),
            "scoutctl setup on Linux leaves ~/Scout without .git",
            known=KNOWN_VAULT_NOT_GIT,
        )
        github = self.cache_root(self.releases.from_version) / "phases" / "connectors" / "github.md"
        uses_bsd = github.exists() and re.search(r"date -v", github.read_text(encoding="utf-8")) is not None
        r.check(
            "phases use no BSD-only `date -v`",
            not uses_bsd,
            "phases/connectors/github.md calls `date -v`, which GNU date rejects",
            known=KNOWN_BSD_DATE,
        )

    # -- invariants of a failure before the vault step (spec §7.6)

    def snapshot(self) -> dict:
        ptr_path = self.home / ".local" / "state" / "scout" / "engine.json"
        return {
            "pointer": sha256(ptr_path),
            "crontab": self.crontab(),
            "shim": sha256(self.home / ".local" / "bin" / "scoutctl"),
            "config_version": self.config_value("plugin", "version_at_last_update"),
        }

    def check_untouched(self, before: dict, label: str) -> None:
        r = self.report
        after = self.snapshot()
        for key in ("pointer", "crontab", "shim"):
            r.check(f"{label}: {key} untouched", before[key] == after[key])
        r.check(
            f"{label}: vault not upgraded",
            before["config_version"] == after["config_version"],
            f"{before['config_version']} -> {after['config_version']}",
        )
        proc = self.scoutctl("version")
        r.check(
            f"{label}: the old engine still answers",
            proc.stdout.strip() == self.releases.from_version,
            proc.stdout.strip() or proc.stderr.strip()[-200:],
        )

    def close(self) -> None:
        subprocess.run(["crontab", "-r"], capture_output=True)
        self.log.close()


# --- scenarios ----------------------------------------------------------------------------------


def result_of(attempt: dict) -> str:
    row = attempt.get("row")
    return row["result"] if row else f"not now: {attempt.get('reason')}"


def finding(health: dict, fid: str) -> dict | None:
    return next((f for f in health.get("findings", []) if f.get("id") == fid), None)


def scenario_happy(h: Host, rel: Releases, render_dates, slot_tz: str) -> None:
    r = h.report
    if not h.install():
        return
    h.known_linux_issues()
    fixture = h.seed(render_dates)
    v = rel.version

    # §7.2 order: auto-update off → not_eligible, nothing fetched.
    h.scoutctl("config", "set-auto-update", "--disabled")
    a = h.self_update()
    r.check(
        "auto-update off → not_eligible(disabled)",
        result_of(a) == "not_eligible" and a["row"]["detail"] == "disabled",
        json.dumps(a),
    )
    h.scoutctl("config", "set-auto-update", "--enabled")

    # §7.2 step 1 / §11: an app-managed engine is refused before any read, fetch or subprocess.
    ptr_path = h.home / ".local" / "state" / "scout" / "engine.json"
    original = ptr_path.read_bytes()
    ptr_path.write_text(json.dumps({**json.loads(original), "managed_by": "scout-app"}, indent=2) + "\n")
    before_calls, before_http, before_tab = (
        len(h.calls("claude-calls.jsonl")),
        len(h.calls("http-calls.jsonl")),
        h.crontab(),
    )
    a = h.self_update("--scheduled", "--force")
    r.check(
        "scout-app engine → not_eligible(managed_by=scout-app), no target",
        result_of(a) == "not_eligible" and a["row"]["detail"] == "managed_by=scout-app" and a["row"]["target"] is None,
        json.dumps(a),
    )
    r.check("scout-app engine: no claude call", len(h.calls("claude-calls.jsonl")) == before_calls)
    r.check("scout-app engine: no network read", len(h.calls("http-calls.jsonl")) == before_http)
    r.check("scout-app engine: crontab untouched", h.crontab() == before_tab)
    ptr_path.write_bytes(original)

    # Nothing newer on stable yet.
    a = h.self_update()
    r.check("stable == installed → up_to_date", result_of(a) == "up_to_date", json.dumps(a))
    r.check(
        "the check read the stable manifest through the fixture",
        any("/stable/.claude-plugin/marketplace.json" in c["url"] for c in h.calls("http-calls.jsonl")),
    )

    # finalize moved stable, but the GitHub release is missing / a prerelease / soaking (§7.4).
    Releases.move_stable(rel.good)
    fetches = len(h.calls("claude-calls.jsonl"))
    for label, hours, pre, want in (
        ("no GitHub release", None, False, "missing"),
        ("a prerelease", 48, True, "prerelease"),
        ("published 1 h ago", 1, False, "soaking until"),
    ):
        h.release(v, hours_ago=hours, prerelease=pre)
        a = h.self_update("--scheduled")
        r.check(
            f"{label} → waiting_release({want})",
            result_of(a) == "waiting_release" and a["row"]["detail"].startswith(want),
            json.dumps(a),
        )
    r.check("waiting_release ran no claude command", len(h.calls("claude-calls.jsonl")) == fetches)

    # §7.3 check: network failures defer twice, then fail on the third.
    h.release(v, hours_ago=48)
    h.flag("offline", True)
    results = [result_of(h.self_update()) for _ in range(3)]
    h.flag("offline", False)
    r.check("offline ×3 → deferred, deferred, failed", results == ["deferred", "deferred", "failed"], str(results))
    code, health = h.health()
    r.check(
        "health: update.failed is red after the network failures",
        (finding(health, "update.failed") or {}).get("severity") == "red",
        json.dumps(health.get("findings")),
    )
    r.check(
        "notify: a red push for update.failed",
        any("update.failed" in p["finding"] and p["severity"] == "red" for p in h.pushes()),
        json.dumps(h.pushes()),
    )

    # Dry run: says what it would do, records and fetches nothing.
    rows, fetches = len(h.outcomes()), len(h.calls("claude-calls.jsonl"))
    proc = h.scoutctl("self-update", "run", "--dry-run")
    r.check(
        "--dry-run says it would update", f"would update {rel.from_version} -> {v}" in proc.stdout, proc.stdout[-300:]
    )
    r.check("--dry-run records no row", len(h.outcomes()) == rows)
    r.check("--dry-run runs no claude command", len(h.calls("claude-calls.jsonl")) == fetches)

    # §7.1: not near a run. 21:20–21:55 local is inside 45 min of the 22:00 dreaming slot.
    a = h.self_update(tz=slot_tz)
    r.check(
        "a slot due within 45 min → deferred",
        result_of(a) == "deferred" and "45 min" in a["row"]["detail"],
        json.dumps(a),
    )
    r.check("deferred is not settled (heartbeat retries)", a.get("settled") is False)
    a = h.self_update("--scheduled", tz=slot_tz)
    r.check(
        "outside off-peak with a recent check → not now, no row",
        a.get("row") is None and "off-peak" in str(a.get("reason")),
        json.dumps(a),
    )

    # §7.1: not while a session holds the vault lock.
    holder = subprocess.Popen(["sleep", "600"])
    lock = h.vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{holder.pid}\n")
    a = h.self_update("--scheduled")
    holder.send_signal(signal.SIGTERM)
    holder.wait()
    lock.unlink(missing_ok=True)
    r.check("vault lock held → deferred", result_of(a) == "deferred" and "lock" in a["row"]["detail"], json.dumps(a))

    # The update itself, as the heartbeat starts it.
    old_root = Path(h.pointer()["engine_root"])
    old_tab = h.crontab()
    for marker in (h.vault / ".scout-cache").glob("self-update-*.done"):
        marker.unlink()  # earlier settled attempts wrote today's; this one must write its own
    a = h.self_update("--scheduled")
    row = a.get("row") or {}
    r.check("published + soaked → applied", row.get("result") == "applied", json.dumps(a))
    r.check(
        "row: from/target/managed_by/step",
        (row.get("from"), row.get("target"), row.get("managed_by"), row.get("step"))
        == (rel.from_version, v, "install.sh", "verify"),
        json.dumps(row),
    )
    r.check(
        "row: install doctor green or yellow after", row.get("doctor") in ("green", "yellow"), str(row.get("doctor"))
    )
    stamp = datetime.now(ZoneInfo(h.tz)).strftime("%Y-%m-%d")
    r.check(
        "the heartbeat's daily marker is written", (h.vault / ".scout-cache" / f"self-update-{stamp}.done").exists()
    )
    argvs = [c["argv"] for c in h.calls("claude-calls.jsonl")]
    r.check(
        "fetch: marketplace update, plugin update, plugin list",
        ["plugin", "marketplace", "update", "scout-plugin"] in argvs
        and ["plugin", "update", PLUGIN_ID, "--scope", "user"] in argvs,
    )
    new_root = h.cache_root(v)
    r.check("venv built in the NEW root", (new_root / ".venv" / "bin" / "scoutctl").exists(), str(new_root))
    r.check(
        "the old root and its venv are left in place", (old_root / ".venv" / "bin" / "scoutctl").exists(), str(old_root)
    )
    ptr = h.pointer()
    r.check(
        "pointer re-pointed at the new engine",
        ptr.get("version") == v
        and Path(ptr.get("engine_root", "")) == new_root
        and ptr.get("managed_by") == "install.sh",
        json.dumps(ptr),
    )
    proc = h.scoutctl("version")
    r.check("the shim runs the new engine", proc.stdout.strip() == v, proc.stdout.strip())
    tab = h.crontab()
    r.check("crontab no longer names the old root", str(old_root) not in tab, tab[:400])
    r.check("crontab still holds Scout's jobs", "scout" in tab.lower() and tab.count("\n") >= old_tab.count("\n"))
    jobs = [line.split() for line in tab.splitlines() if re.match(r"^[\d*@]", line.strip())]
    missing = [job[5] for job in jobs if len(job) > 5 and not os.access(job[5].strip("'\""), os.X_OK)]
    r.check("every cron job's command is executable", bool(jobs) and not missing, str(missing or tab[:300]))
    r.check(
        "vault upgraded: plugin.version_at_last_update",
        h.config_value("plugin", "version_at_last_update") == v,
        str(h.config_value("plugin", "version_at_last_update")),
    )
    changed = [rel_path for rel_path, digest in fixture.items() if sha256(h.vault / rel_path) != digest]
    r.check("fixture vault content untouched by the upgrade", not changed, str(changed))
    code, health = h.health()
    last = finding(health, "update.last")
    r.check("health: update.last note names the update", bool(last) and v in last.get("summary", ""), json.dumps(last))
    r.check(
        "health: no update.failed after the update",
        finding(health, "update.failed") is None,
        json.dumps(health.get("findings")),
    )
    r.info(
        "health severity after the update",
        f"{health.get('severity')} (exit {code}): "
        + ", ".join(f"{f['id']}={f['severity']}" for f in health.get("findings", [])),
    )
    r.check("install doctor after the update is not red", h.doctor_severity() in ("green", "yellow"))
    titles = [p["title"] for p in h.pushes()]
    r.check("notify: 'Scout updated itself' push", any(f"updated itself to {v}" in t for t in titles), str(titles))
    r.check(
        "notify: a recovery push after the red",
        any("recover" in (p["title"] + p["body"]).lower() for p in h.pushes()),
        str(titles),
    )
    r.info(
        "desktop channel (notify-send) calls",
        str(
            len((h.state / "notify-send.log").read_text().splitlines()) if (h.state / "notify-send.log").exists() else 0
        ),
    )

    # §7.5: one applied version per 24 h for scheduled runs; a manual run is up to date.
    rows = len(h.outcomes())
    a = h.self_update("--scheduled")
    r.check(
        "scheduled again → not now (applied in the last 24 h), no row",
        a.get("row") is None and "24 h" in str(a.get("reason")) and len(h.outcomes()) == rows,
        json.dumps(a),
    )
    a = h.self_update()
    r.check("manual run on the new engine → up_to_date", result_of(a) == "up_to_date", json.dumps(a))


def scenario_rollback(h: Host, rel: Releases, render_dates) -> None:
    """§7.6: a failure before the vault step leaves the old engine live and pointed to."""
    r = h.report
    if not h.install():
        return
    h.seed(render_dates)
    v = rel.version
    Releases.move_stable(rel.broken)
    h.release(v, hours_ago=48)
    before = h.snapshot()

    h.flag("fail-plugin-update", True)
    a = h.self_update()
    h.flag("fail-plugin-update", False)
    r.check(
        "plugin update fails → failed(fetch)", result_of(a) == "failed" and a["row"]["step"] == "fetch", json.dumps(a)
    )
    h.check_untouched(before, "after a fetch failure")

    a = h.self_update()
    r.check(
        "retry within 24 h → not now (backoff), no row",
        a.get("row") is None and "24 h" in str(a.get("reason")),
        json.dumps(a),
    )

    a = h.self_update("--force")
    r.check(
        "broken install-venv.sh → failed(venv)",
        result_of(a) == "failed" and a["row"]["step"] == "venv" and "install-venv.sh exited" in a["row"]["detail"],
        json.dumps(a),
    )
    h.check_untouched(before, "after a venv failure")
    r.check("the old engine's install doctor is not red", h.doctor_severity() in ("green", "yellow"))
    listed = json.loads(h.run(["claude", "plugin", "list", "--json"]).stdout or "[]")
    r.info(
        "Claude Code's registry after the failure (spec §7.6 leftover)",
        f"names {listed[0]['version'] if listed else '?'}; scheduled runs and the shim stay on {rel.from_version}",
    )
    code, health = h.health()
    failed = finding(health, "update.failed") or {}
    r.check(
        "health: update.failed red, names the step",
        failed.get("severity") == "red" and "venv" in json.dumps(failed),
        json.dumps(failed),
    )
    r.check("health exits non-zero on red", code != 0, f"exit {code}")
    r.check(
        "notify: a red push for update.failed",
        any("update.failed" in p["finding"] and p["severity"] == "red" for p in h.pushes()),
        json.dumps(h.pushes()),
    )

    a = h.self_update("--force")
    r.check("third failure of the same target recorded", result_of(a) == "failed", json.dumps(a))
    a = h.self_update()
    r.check(
        "after 3 failures → not now until a newer release or --force",
        a.get("row") is None and "failed 3 times" in str(a.get("reason")),
        json.dumps(a),
    )

    # The fix ships as the next patch: a newer target resets the backoff, no --force needed.
    Releases.move_stable(rel.next)
    h.release(rel.next_version, hours_ago=48)
    a = h.self_update()
    r.check(
        f"fixed release {rel.next_version} → applied without --force",
        result_of(a) == "applied" and a["row"]["target"] == rel.next_version,
        json.dumps(a),
    )
    r.check("the shim runs the fixed engine", h.scoutctl("version").stdout.strip() == rel.next_version)
    code, health = h.health()
    r.check(
        "health: update.failed cleared", finding(health, "update.failed") is None, json.dumps(health.get("findings"))
    )


def scenario_sidecars(h: Host, rel: Releases, render_dates) -> None:
    """§7.7: a conflicting brain edit is parked in a sidecar; a parser sidecar holds the update."""
    r = h.report
    if not h.install():
        return
    h.seed(render_dates)
    v = rel.version
    skill = h.vault / "SKILL.md"
    lines = skill.read_text(encoding="utf-8").splitlines()
    counts = {ln: lines.count(ln) for ln in lines}
    phase = rel.phase_lines()
    line = next(
        (
            ln
            for ln in lines
            if len(ln) > 40
            and counts[ln] == 1
            and phase.get(ln) == 1
            and not ln.lstrip().startswith(("#", "```", "|"))
            and "{{" not in ln
        ),
        None,
    )
    if not r.check("found a SKILL.md line that comes from exactly one phase file", line is not None) or line is None:
        return
    sha = rel.with_phase_change(line, line + " (e2e: changed upstream)")
    skill.write_text(
        skill.read_text(encoding="utf-8").replace(line, line + " (e2e: edited locally)", 1), encoding="utf-8"
    )
    Releases.move_stable(sha)
    h.release(v, hours_ago=48)

    parser_sidecar = h.vault / "knowledge-base" / "ontology" / "parser.py.proposed-merge"
    parser_sidecar.parent.mkdir(parents=True, exist_ok=True)
    parser_sidecar.write_text("# e2e: an unresolved parser merge\n")
    a = h.self_update()
    r.check(
        "a pending parser sidecar → held", result_of(a) == "held" and "parser.py" in a["row"]["detail"], json.dumps(a)
    )
    code, health = h.health()
    r.check(
        "health: update.held is yellow",
        (finding(health, "update.held") or {}).get("severity") == "yellow",
        json.dumps(health.get("findings")),
    )
    parser_sidecar.unlink()

    a = h.self_update()
    row = a.get("row") or {}
    r.check(
        "conflicting brain edit → applied_with_sidecars", row.get("result") == "applied_with_sidecars", json.dumps(a)
    )
    r.check("row lists the SKILL.md sidecar", any("SKILL" in s for s in row.get("sidecars", [])), json.dumps(row))
    r.check("the sidecar file exists", (h.vault / "SKILL.md.proposed-merge").exists())
    r.check("the live SKILL.md keeps the local edit", "(e2e: edited locally)" in skill.read_text(encoding="utf-8"))
    r.check("the engine moved on regardless", h.scoutctl("version").stdout.strip() == v)
    code, health = h.health()
    r.check(
        "health: update.sidecars is yellow",
        (finding(health, "update.sidecars") or {}).get("severity") == "yellow",
        json.dumps(health.get("findings")),
    )
    r.check(
        "notify: a 'files to merge' push",
        any("files to merge" in p["title"] for p in h.pushes()),
        str([p["title"] for p in h.pushes()]),
    )


# --- main -----------------------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--as-version", default=None, help="Stamp the target as this X.Y.Z (default: only when not newer).")
    args = ap.parse_args()
    wanted = [s for s in args.scenarios.split(",") if s]
    unknown = set(wanted) - set(SCENARIOS)
    if unknown:
        raise SystemExit(f"unknown scenario(s): {', '.join(sorted(unknown))}")

    (WORK / "logs").mkdir(parents=True, exist_ok=True)
    report = Report(WORK / "report.json")
    rel = Releases(args.as_version)
    if semver(rel.from_version) < (0, 18, 0):
        raise SystemExit(f"--from must be v0.18.0 or later (headless updates ship in 0.18.0); got {rel.from_version}")
    report.info(
        "versions",
        f"from {rel.from_version} -> target {rel.version}" + (" (stamped onto the target ref)" if rel.stamped else ""),
    )
    render_dates = load_render_dates()
    quiet_tz = zone_where((2, 30), (4, 30))  # off-peak, and hours from any default slot
    slot_tz = zone_where((21, 20), (21, 55))  # inside 45 min of the 22:00 dreaming slot
    report.info("zones", f"quiet={quiet_tz} slot-due={slot_tz}")

    for name in wanted:
        print(f"\n== {name}", flush=True)
        report.scenario = name
        host = Host(name, report, rel, quiet_tz)
        started = time.monotonic()
        try:
            if name == "happy":
                scenario_happy(host, rel, render_dates, slot_tz)
            elif name == "rollback":
                scenario_rollback(host, rel, render_dates)
            else:
                scenario_sidecars(host, rel, render_dates)
        except Exception as e:  # noqa: BLE001 - one broken scenario must not hide the others' results
            report.check("scenario ran to the end", False, f"{type(e).__name__}: {e}")
        finally:
            host.close()
        report.info("duration", f"{time.monotonic() - started:.0f} s")

    counts = {s: sum(1 for r in report.rows if r["status"] == s) for s in ("PASS", "FAIL", "KNOWN")}
    print(
        f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['KNOWN']} known issue(s). "
        f"Report: {report.path}; logs: {WORK / 'logs'}",
        flush=True,
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
