"""The environment an eval sample's runner runs in (spec 2026-10-12 §4.1).

HOME stays real: isolating it loses the keychain login (spec §4.3). So the
paths that reach through it are closed one by one here: the stub ``gh`` is
first on PATH and ``GH_CONFIG_DIR`` is a temp dir (backstop if a shell re-adds
the real ``gh``), global git config is off, and no inherited ``GIT_*``,
``SCOUT_*`` or ``CLAUDE_CONFIG_DIR`` leaks in.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[3]
STUB_BIN = ENGINE / "tests" / "eval" / "stubs" / "bin"
SYSTEM_PATH = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
_DROPPED_PREFIXES = ("GIT_", "SCOUT_", "CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE")


def build_env(
    *,
    base: Mapping[str, str],
    mode: str,
    result_dir: Path,
    mcp_config: Path,
    allowed_servers: Iterable[str],
    allowed_tools: Iterable[str],
    model: str,
    real_claude: str,
    script: Path,
    today: str,
    timeout_s: int,
) -> dict[str, str]:
    env = {k: v for k, v in base.items() if not k.startswith(_DROPPED_PREFIXES)}
    path = [str(STUB_BIN), str(ENGINE / ".venv" / "bin"), str(Path(real_claude).parent), *SYSTEM_PATH]
    env.update(
        PATH=":".join(dict.fromkeys(path)),
        SCOUT_FORCE_MODE=mode,
        SCOUT_RETRY_DISABLE="1",
        TZ="UTC",
        SCOUT_USER_TIMEZONE="UTC",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GH_CONFIG_DIR=str(result_dir / "gh-config"),
        SCOUT_EVAL_RESULT_DIR=str(result_dir),
        SCOUT_EVAL_REAL_CLAUDE=real_claude,
        SCOUT_EVAL_MCP_CONFIG=str(mcp_config),
        SCOUT_EVAL_ALLOWED_SERVERS=",".join(sorted(allowed_servers)),
        SCOUT_EVAL_ALLOWED_TOOLS=",".join(allowed_tools),
        SCOUT_EVAL_MODEL=model,
        SCOUT_EVAL_TIMEOUT_S=str(timeout_s),
        SCOUT_EVAL_PYTHON=sys.executable,
        SCOUT_EVAL_ENGINE=str(ENGINE),
        SCOUT_EVAL_SCRIPT=str(script),
        SCOUT_EVAL_CALLS=str(result_dir / "calls.jsonl"),
        SCOUT_EVAL_TODAY=today,
    )
    return env
