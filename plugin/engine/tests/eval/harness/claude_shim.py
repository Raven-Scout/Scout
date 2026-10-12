"""The ``CLAUDE_BIN`` an eval vault's runner calls (spec 2026-10-12 §4.1).

``run-scout.sh`` runs ``claude-with-retry.sh $LOG $CLAUDE_BIN <runner flags>
-p "$PROMPT"``. This shim takes those flags, drops the runner's permission
mode and model, keeps ``--max-budget-usd`` and ``--name``, and runs the real
``claude`` isolated the way #354 isolates connector drafting, plus what an
eval needs:

- a fresh ``CLAUDE_CONFIG_DIR`` per sample, with
  ``CLAUDE_SECURESTORAGE_CONFIG_DIR=""`` so the keychain login (the only thing
  shared) stays reachable;
- ``--setting-sources ""`` and hooks off;
- Claude Code's Bash sandbox (spec §12 R2a′): a Bash command writes only
  inside the sample's result dir and reaches no network host, and nothing
  runs outside it. Temp files go to ``<result>/tmp``;
- ``--strict-mcp-config`` with the stub servers. Without it the account's
  live claude.ai connectors load (verified, spec §4.3);
- ``dontAsk`` with the eval allowlist;
- stream-json, tee'd to ``<result>/stream.jsonl``.

**Init-event guard.** The first ``system/init`` event must list every stub
server as ``connected`` and nothing else. Otherwise the session is killed
before its first turn and ``aborted.json`` says why: exit 3. A timeout kills
it too: exit 4. Missing eval environment: exit 2.

The prompt goes on stdin: ``--allowedTools`` is variadic and would swallow a
positional prompt.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .runner_env import SECRETS

SHIM = Path(__file__).resolve().parents[1] / "stubs" / "shim" / "claude-shim"
REQUIRED_ENV = (
    "SCOUT_EVAL_RESULT_DIR",
    "SCOUT_EVAL_REAL_CLAUDE",
    "SCOUT_EVAL_MCP_CONFIG",
    "SCOUT_EVAL_ALLOWED_SERVERS",
    "SCOUT_EVAL_ALLOWED_TOOLS",
    "SCOUT_EVAL_MODEL",
    "SCOUT_EVAL_TIMEOUT_S",
)
_DROPPED_WITH_VALUE = {"--permission-mode", "--model"}
_KEPT_WITH_VALUE = {"--max-budget-usd", "--name"}
EXIT_ABORTED, EXIT_TIMEOUT, EXIT_ENV = 3, 4, 2
# The other sessions' trees under Claude Code's shared temp root, which the sandbox
# leaves writable. Only the per-project dirs: the Bash tool writes its own cwd
# file at the root's top level, and denying that fails every command (verified).
_SHARED_TMP_DENY = ["/tmp/claude*/-*", "/private/tmp/claude*/-*"]


def settings(result_dir: str) -> dict[str, Any]:
    """The ``--settings`` JSON. Under ``--setting-sources ""`` it is the only settings source."""
    return {
        "disableAllHooks": True,
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,  # an unsandboxed eval session must not start
            "autoAllowBashIfSandboxed": False,  # the eval allowlist still decides what runs
            "allowUnsandboxedCommands": False,  # dangerouslyDisableSandbox is ignored
            "filesystem": {"allowWrite": [result_dir], "denyWrite": _SHARED_TMP_DENY},
            "network": {"allowedDomains": [], "strictAllowlist": True},
        },
    }


def build_command(
    runner_argv: list[str], *, real_claude: str, model: str, mcp_config: str, allowed_tools: str, result_dir: str
) -> tuple[list[str], str]:
    """The isolated claude argv for these runner flags, and the prompt to send on stdin."""
    kept: list[str] = []
    prompt = ""
    args = iter(runner_argv)
    for arg in args:
        if arg == "-p":
            prompt = next(args, "")
        elif arg in _DROPPED_WITH_VALUE:
            next(args, None)
        elif arg in _KEPT_WITH_VALUE:
            kept += [arg, next(args, "")]
    cmd = [
        real_claude,
        "-p",
        "--model",
        model,
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        allowed_tools,
        "--setting-sources",
        "",
        "--settings",
        json.dumps(settings(result_dir)),
        "--strict-mcp-config",
        "--mcp-config",
        mcp_config,
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--disable-slash-commands",
        *kept,
    ]
    return cmd, prompt


def guard_init(event: dict[str, Any], allowed: set[str]) -> dict[str, Any] | None:
    """Why an init event must abort the session, or None when it is safe."""
    if event.get("type") != "system" or event.get("subtype") != "init":
        return {"reason": "no_init", "first_event": f"{event.get('type')}/{event.get('subtype')}"}
    source = event.get("apiKeySource")
    if source != "none":  # anything but the subscription login would bill (spec §13 Q1)
        return {"reason": "api_key", "source": source}
    servers = {s.get("name", ""): s.get("status") for s in event.get("mcp_servers") or []}
    leaked = sorted(set(servers) - allowed)
    if leaked:
        return {"reason": "mcp_leak", "servers": leaked}
    down = sorted(name for name in allowed if servers.get(name) != "connected")
    if down:
        return {"reason": "stub_not_connected", "servers": down}
    return None


def _abort(result_dir: Path, why: dict[str, Any]) -> None:
    (result_dir / "aborted.json").write_text(json.dumps(why), encoding="utf-8")


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:  # already gone (ProcessLookupError), or a reaped group (PermissionError)
        pass


def run(runner_argv: list[str], env: dict[str, str]) -> int:
    missing = [k for k in REQUIRED_ENV if not env.get(k)]
    if missing:
        print(f"claude-shim: not an eval run, missing {', '.join(missing)}", file=sys.stderr)
        return EXIT_ENV
    result_dir = Path(env["SCOUT_EVAL_RESULT_DIR"]).resolve()  # the sandbox matches real paths
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "tmp").mkdir(exist_ok=True)
    allowed = {s for s in env["SCOUT_EVAL_ALLOWED_SERVERS"].split(",") if s}
    cmd, prompt = build_command(
        runner_argv,
        real_claude=env["SCOUT_EVAL_REAL_CLAUDE"],
        model=env["SCOUT_EVAL_MODEL"],
        mcp_config=env["SCOUT_EVAL_MCP_CONFIG"],
        allowed_tools=env["SCOUT_EVAL_ALLOWED_TOOLS"],
        result_dir=str(result_dir),
    )
    child_env = {k: v for k, v in env.items() if k not in SECRETS and k != "PYTHONPATH"}
    if env.get("SCOUT_EVAL_SESSION_PYTHONPATH"):  # a base arm: the session's scoutctl is the base's
        child_env["PYTHONPATH"] = env["SCOUT_EVAL_SESSION_PYTHONPATH"]
    child_env.update(
        CLAUDE_CONFIG_DIR=str(result_dir / "claude-config"),
        CLAUDE_SECURESTORAGE_CONFIG_DIR="",
        CLAUDE_CODE_TMPDIR=str(result_dir / "tmp"),  # TMPDIR for Bash: the default, /tmp/claude, is shared
    )
    # claude runs in a process group of its own (so the guard and the timeout can kill it with its
    # MCP stubs), which means signalling the runner's group never reaches it. Whoever stops this
    # shim, the runner on an overrun or Ctrl-C, must take claude down with it, including when the
    # signal lands before claude has been started.
    started: list[subprocess.Popen[str]] = []
    terminated = threading.Event()

    def _on_terminate(signum: int, _frame: object) -> None:
        terminated.set()
        for child in started:
            _kill_group(child.pid)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, _on_terminate)
    signal.signal(signal.SIGINT, _on_terminate)
    with (result_dir / "claude.stderr").open("w", encoding="utf-8") as err:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=err,
            env=child_env,
            text=True,
            start_new_session=True,
        )
    started.append(proc)
    if terminated.is_set():  # the signal arrived while claude was being started
        _kill_group(proc.pid)
        return 128 + signal.SIGTERM
    # The runner's backstop if this shim is killed outright (SIGKILL can't be handled).
    (result_dir / "claude.pgid").write_text(str(proc.pid), encoding="utf-8")
    timed_out = threading.Event()

    def _kill() -> None:
        _kill_group(proc.pid)

    def _on_timeout() -> None:
        timed_out.set()
        _kill()

    timer = threading.Timer(float(env["SCOUT_EVAL_TIMEOUT_S"]), _on_timeout)
    timer.daemon = True
    timer.start()
    assert proc.stdin is not None and proc.stdout is not None
    result_text = ""
    checked = False
    try:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:  # the child died before reading its prompt; its exit code says why
            pass
        with (result_dir / "stream.jsonl").open("w", encoding="utf-8") as out:
            for line in proc.stdout:
                out.write(line)
                out.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not checked:  # the first event must be a clean init, or nothing runs
                    checked = True
                    why = guard_init(event, allowed)
                    if why:
                        _kill()
                        proc.wait()
                        _abort(result_dir, why)
                        return EXIT_ABORTED
                if event.get("type") == "result":
                    result_text = str(event.get("result") or "")
        code = proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        _abort(result_dir, {"reason": "timeout", "seconds": float(env["SCOUT_EVAL_TIMEOUT_S"])})
        return EXIT_TIMEOUT
    print(result_text)
    return code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], dict(os.environ)))
