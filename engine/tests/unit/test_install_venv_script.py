"""Behavioral test for scripts/install-venv.sh with a fake uv (spec E5)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "install-venv.sh"
PLUGIN_ROOT = SCRIPT.parents[1]


def _fake_uv(tmp_path: Path) -> Path:
    """Records every invocation and fakes the two things the script needs:
    `uv venv` creates bin/python, `uv pip install` creates bin/scoutctl."""
    uv = tmp_path / "fakebin" / "uv"
    uv.parent.mkdir()
    uv.write_text(
        dedent(
            """\
            #!/bin/bash
            echo "uv $*" >> "$FAKE_UV_LOG"
            case "$1" in
              venv)
                target="${@: -1}"
                mkdir -p "$target/bin"
                printf '#!/bin/sh\\n' > "$target/bin/python"
                chmod +x "$target/bin/python" ;;
              pip)
                py=""; while [ $# -gt 0 ]; do
                  [ "$1" = "--python" ] && py="$2"; shift; done
                printf '#!/bin/sh\\n' > "$(dirname "$py")/scoutctl"
                chmod +x "$(dirname "$py")/scoutctl" ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    uv.chmod(0o755)
    return uv


def test_uses_uv_into_scout_venv_dir_with_requested_extras(tmp_path):
    uv = _fake_uv(tmp_path)
    venv = tmp_path / "share" / "scout" / "venv" / "0.10.0"
    log = tmp_path / "uv.log"
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            "HOME": str(tmp_path / "home"),
            "PATH": "/usr/bin:/bin",
            "SCOUT_UV": str(uv),
            "SCOUT_VENV_DIR": str(venv),
            "SCOUT_VENV_EXTRAS": "full",
            "FAKE_UV_LOG": str(log),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = log.read_text().splitlines()
    assert lines[0] == f"uv venv --python 3.12 {venv}"
    assert lines[1] == f"uv pip install --python {venv}/bin/python --quiet -e {PLUGIN_ROOT}/engine[full]"
    assert (venv / "bin" / "scoutctl").exists()
    assert f"ok: venv ready at {venv}" in proc.stdout


def test_defaults_to_plugin_root_venv_and_dev_extras(tmp_path, monkeypatch):
    """Default location is unchanged so /scout-update keeps working; run the
    script from a COPY of the tree so the developer's real .venv is untouched."""
    import shutil

    root = tmp_path / "plugin"
    (root / "engine").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy(SCRIPT, root / "scripts" / "install-venv.sh")
    uv = _fake_uv(tmp_path)
    log = tmp_path / "uv.log"
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "install-venv.sh")],
        env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin", "SCOUT_UV": str(uv), "FAKE_UV_LOG": str(log)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"uv venv --python 3.12 {root}/.venv" in log.read_text()
    assert f"-e {root}/engine[dev]" in log.read_text()
