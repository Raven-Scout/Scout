"""No test may reach the real launchd: gui/<uid> is global to the user, so a temp HOME doesn't isolate it.

During a test run, a bootstrap install without --no-jobs once replaced the maintainer's live
scheduler jobs. tests/conftest.py puts a refusing `launchctl` first on PATH for every test. Code
that needs launchctl behaviour mocks subprocess instead.
"""

from __future__ import annotations

import os
import subprocess
import sys


def test_launchctl_on_path_refuses_in_tests() -> None:
    done = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}"], capture_output=True, text=True)
    assert done.returncode == 99
    assert "blocked in tests" in done.stderr


def test_a_child_process_gets_the_refusing_launchctl_too() -> None:
    code = "import subprocess; print(subprocess.run(['launchctl', 'list'], capture_output=True).returncode)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ))
    assert out.stdout.strip() == "99"
