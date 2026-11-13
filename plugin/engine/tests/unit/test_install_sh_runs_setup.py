"""install.sh ends by running scoutctl setup with every argument it was given (spec §5)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
INSTALL_SH = REPO / "install.sh"


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


def _world(tmp_path: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "home"
    home.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    root = tmp_path / "plugin-root"
    (root / "scripts").mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "scout", "version": "0.18.1"}')
    log = tmp_path / "setup.log"
    _stub(
        root / "scripts" / "install-venv.sh",
        f'd="$(cd "$(dirname "$0")/.." && pwd)/.venv/bin"; mkdir -p "$d"\n'
        "cat > \"$d/scoutctl\" <<'STUB'\n"
        "#!/bin/bash\n"
        '[ "$1" = --help ] && exit 0\n'
        # One argument per line, so a split argument shows up as two lines.
        f"printf '%s\\n' \"$@\" >> '{log}'\n"
        "STUB\n"
        'chmod +x "$d/scoutctl"\n',
    )
    listing = json.dumps([{"id": "scout@scout-plugin", "installPath": str(root)}])
    _stub(bin_ / "claude", f"[ \"$1 $2\" = 'plugin list' ] && echo '{listing}'\nexit 0\n")
    _stub(bin_ / "uv", f'while [ $# -gt 0 ] && [ "$1" != python ]; do shift; done; shift; exec {sys.executable} "$@"\n')
    _stub(bin_ / "xcode-select", "exit 0\n")
    _stub(bin_ / "git", "exit 0\n")
    env = {
        "HOME": str(home),
        "PATH": f"{bin_}:/usr/bin:/bin",
        "SCOUT_KNOWN_MARKETPLACES": str(tmp_path / "none.json"),
        # Never reach GitHub from a test: a file:// URL that doesn't exist, unless a test writes it.
        "SCOUT_RELEASE_MANIFEST_URL": (tmp_path / "scout-release.json").as_uri(),
    }
    return env, log


def test_install_sh_hands_every_argument_to_setup(tmp_path):
    env, log = _world(tmp_path)
    done = subprocess.run(
        ["bash", str(INSTALL_SH), "--yes", "--name", "Alex Example", "--email", "alex@example.com"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert log.read_text().splitlines() == [
        "setup",
        "--managed-by",
        "install.sh",
        "--yes",
        "--name",
        "Alex Example",  # one argument, not two
        "--email",
        "alex@example.com",
    ]
    assert "/scout-setup" not in done.stdout + done.stderr


def test_check_mode_never_runs_setup(tmp_path):
    env, log = _world(tmp_path)
    done = subprocess.run(["bash", str(INSTALL_SH), "--check"], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert not log.exists()


def test_the_closing_text_mentions_the_app_only_on_macos():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert 'exec "$ROOT/.venv/bin/scoutctl" setup --managed-by install.sh "$@"' in text
    assert "/scout-setup" not in text
    guard = re.search(r'\nif \[ "\$\(uname -s\)" = "Darwin" \]; then\n(.*?)\nfi\n', text, re.S)
    assert guard is not None, "no Darwin-only block"
    assert "Scout.app" in guard.group(1)
    assert text.count("Scout.app") == guard.group(1).count("Scout.app"), "an app hint outside the Darwin guard"


def _install(tmp_path, manifest: str | None = None, **extra: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    env, log = _world(tmp_path)
    if manifest is not None:
        (tmp_path / "scout-release.json").write_text(manifest)
    env.update(extra)
    done = subprocess.run(["bash", str(INSTALL_SH), "--yes"], env=env, capture_output=True, text=True, timeout=60)
    return done, log


def test_release_check_confirms_the_latest_version(tmp_path):
    done, log = _install(tmp_path, '{"name": "scout", "version": "0.18.1", "tag": "v0.18.1"}')
    assert done.returncode == 0, done.stderr
    assert "Scout 0.18.1 is the latest release." in done.stdout
    assert "warning:" not in done.stderr
    assert log.read_text().splitlines()[0] == "setup"


def test_release_check_warns_on_a_stale_install_and_still_runs_setup(tmp_path):
    done, log = _install(tmp_path, '{"name": "scout", "version": "0.19.0", "tag": "v0.19.0"}')
    assert done.returncode == 0, done.stderr
    assert "installed Scout 0.18.1, but the latest release is 0.19.0" in done.stderr
    assert "claude plugin marketplace update scout-plugin" in done.stderr
    assert log.read_text().splitlines()[0] == "setup"


def test_an_unreachable_release_page_never_blocks_the_install(tmp_path):
    done, log = _install(tmp_path)  # the file:// URL doesn't exist
    assert done.returncode == 0, done.stderr
    assert "could not reach GitHub Releases" in done.stdout
    assert log.read_text().splitlines()[0] == "setup"


def test_a_garbled_manifest_is_ignored(tmp_path):
    done, log = _install(tmp_path, "<html>not json</html>")
    assert done.returncode == 0, done.stderr
    assert "latest release" not in done.stdout + done.stderr
    assert log.read_text().splitlines()[0] == "setup"


def test_the_release_check_can_be_switched_off(tmp_path):
    done, _ = _install(tmp_path, '{"version": "0.19.0"}', SCOUT_NO_RELEASE_CHECK="1")
    assert done.returncode == 0, done.stderr
    assert "latest release" not in done.stdout + done.stderr
    assert "could not reach" not in done.stdout


def test_the_default_manifest_url_is_the_latest_release_asset():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "https://github.com/$MARKETPLACE/releases/latest/download/scout-release.json" in text
