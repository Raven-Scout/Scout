"""install.sh ends by running scoutctl setup with every argument it was given (spec §5)."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

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


def _install(
    tmp_path, manifest: str | None = None, *args: str, curl: str | None = None, **extra: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run install.sh in a stub world. ``curl`` replaces curl with a stub body; it sees $CURL_ARGS."""
    env, log = _world(tmp_path)
    if manifest is not None:
        (tmp_path / "scout-release.json").write_text(manifest)
    if curl is not None:
        env["CURL_ARGS"] = str(tmp_path / "curl.args")
        # One argument per line, one blank line per call.
        _stub(tmp_path / "bin" / "curl", "printf '%s\\n' \"$@\" '' >> \"$CURL_ARGS\"\n" + curl)
    env.update(extra)
    argv = ["bash", str(INSTALL_SH), *(args or ("--yes",))]
    done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
    return done, log


def _curl_calls(tmp_path: Path) -> list[list[str]]:
    f = tmp_path / "curl.args"
    if not f.exists():
        return []
    return [call.splitlines() for call in f.read_text().split("\n\n") if call.strip()]


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
    # The documented update path (README "Updating later"), not the commands the installer just ran.
    assert "/scout-update" in done.stderr
    assert "Setup continues with 0.18.1" in done.stderr
    assert log.read_text().splitlines()[0] == "setup"


def test_an_install_newer_than_the_latest_release_says_nothing(tmp_path):
    # A release candidate or a local checkout: not stale, so no warning and no "latest" claim.
    done, log = _install(tmp_path, '{"name": "scout", "version": "0.18.0", "tag": "v0.18.0"}')
    assert done.returncode == 0, done.stderr
    assert "latest release" not in done.stdout + done.stderr
    assert log.read_text().splitlines()[0] == "setup"


def test_versions_compare_as_numbers_not_strings(tmp_path):
    done, _ = _install(tmp_path, '{"version": "0.18.10"}')
    assert "installed Scout 0.18.1, but the latest release is 0.18.10" in done.stderr


def test_an_unreachable_release_page_never_blocks_the_install(tmp_path):
    done, log = _install(tmp_path)  # the file:// URL doesn't exist
    assert done.returncode == 0, done.stderr
    assert "could not reach GitHub Releases" in done.stdout
    assert log.read_text().splitlines()[0] == "setup"


@pytest.mark.parametrize("exit_code", [6, 7, 28, 35], ids=["no-dns", "refused", "timeout", "tls"])
def test_offline_or_failing_curl_never_blocks_the_install(tmp_path, exit_code):
    done, log = _install(tmp_path, curl=f"exit {exit_code}\n")
    assert done.returncode == 0, done.stderr
    assert "could not reach GitHub Releases" in done.stdout
    assert len(_curl_calls(tmp_path)) == 1, "a failed check must not retry"
    assert log.read_text().splitlines()[0] == "setup"


def test_a_server_that_never_answers_costs_seconds_not_the_install(tmp_path):
    # Real curl against a socket that accepts and then says nothing: --max-time has to end it.
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        url = f"http://127.0.0.1:{server.getsockname()[1]}/scout-release.json"
        started = time.monotonic()
        done, log = _install(tmp_path, SCOUT_RELEASE_MANIFEST_URL=url)
        took = time.monotonic() - started
    assert done.returncode == 0, done.stderr
    assert "could not reach GitHub Releases" in done.stdout
    assert log.read_text().splitlines()[0] == "setup"
    assert took < 20, f"the release check held the install for {took:.0f}s"


@pytest.mark.parametrize(
    "manifest",
    [
        "<html>not json</html>",
        "",
        "[]",
        "{}",
        '{"version": 19}',
        '{"version": "latest"}',
        '{"version": "0.19.0-rc.1"}',
        '{"version": "0.19.0\\u001b[2J"}',
    ],
    ids=["html", "empty", "list", "no-version", "number", "word", "prerelease", "escape-sequence"],
)
def test_a_malformed_manifest_is_ignored(tmp_path, manifest):
    done, log = _install(tmp_path, manifest)
    assert done.returncode == 0, done.stderr
    assert "latest release" not in done.stdout + done.stderr
    assert "\x1b" not in done.stdout + done.stderr
    assert log.read_text().splitlines()[0] == "setup"


@pytest.mark.parametrize("opt_out", [{"SCOUT_NO_RELEASE_CHECK": "1"}, {"DO_NOT_TRACK": "1"}])
def test_the_release_check_can_be_switched_off(tmp_path, opt_out):
    done, log = _install(tmp_path, '{"version": "0.19.0"}', curl="exit 0\n", **opt_out)
    assert done.returncode == 0, done.stderr
    assert _curl_calls(tmp_path) == [], "an opted-out install made a request"
    assert "latest release" not in done.stdout + done.stderr
    assert "could not reach" not in done.stdout
    assert log.read_text().splitlines()[0] == "setup"


def test_check_only_makes_no_request(tmp_path):
    done, _ = _install(tmp_path, None, "--check", curl="exit 0\n")
    assert done.returncode == 0, done.stderr
    assert _curl_calls(tmp_path) == []


def test_the_release_check_is_one_plain_bounded_get(tmp_path):
    done, _ = _install(tmp_path, curl='echo \'{"version": "0.18.1"}\'\n')
    assert done.returncode == 0, done.stderr
    assert "Scout 0.18.1 is the latest release." in done.stdout
    (call,) = _curl_calls(tmp_path)
    # -q must come first, or curl has already read ~/.curlrc (which could add a user agent, cookies, retries).
    assert call[0] == "-q"
    url = call[-1]
    assert url == (tmp_path / "scout-release.json").as_uri()
    assert "?" not in url and "#" not in url
    assert int(call[call.index("--max-time") + 1]) <= 10
    assert int(call[call.index("--connect-timeout") + 1]) <= 5
    sending = {"-A", "--user-agent", "-H", "--header", "-b", "--cookie", "-c", "--cookie-jar"}
    sending |= {"-d", "--data", "-e", "--referer", "-u", "--user"}
    assert not sending & set(call), f"the request sends more than a plain GET: {call}"
    assert not any(a.startswith("--retry") for a in call), "no retries: one try, then move on"


def test_the_default_manifest_url_is_the_latest_release_asset():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "https://github.com/$MARKETPLACE/releases/latest/download/scout-release.json" in text
    assert "SCOUT_NO_RELEASE_CHECK" in text.split("set -euo pipefail")[0], "the opt-out isn't in the header"


def test_privacy_pages_document_both_opt_outs():
    for page in (REPO / "PRIVACY.md", REPO / "docs" / "privacy.html"):
        text = page.read_text(encoding="utf-8")
        assert "SCOUT_NO_RELEASE_CHECK=1" in text, page
        assert "DO_NOT_TRACK=1" in text, page
