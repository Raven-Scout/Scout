"""Test helper: make a fake ``claude`` script answer the runners' gate probe like a claude whose hooks load.

The runners run ``scoutctl gates probe`` before each session (spec 2026-11-13 §3.5).
A fake claude that stands for a real, hooked claude must pass it: a call without
``--name`` is the probe (or ``--version``), so the prelude answers the fake
endpoint's scripted Bash call with the kb-read-gate's deny and exits. Put
:data:`HOOKED_CLAUDE_PRELUDE` right after the fake's imports (it needs ``json``,
``os`` and ``sys``).
"""

HOOKED_CLAUDE_PRELUDE = """
if "--name" not in sys.argv:
    import urllib.request
    if "--version" in sys.argv:
        print("9.9.9 (Claude Code)"); sys.exit(0)
    def _post(body):
        req = urllib.request.Request(os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"}, method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    _msgs = [{"role": "user", "content": "probe"}]
    _tools = [{"name": "Bash", "input_schema": {}}]
    _block = _post({"model": "m", "tools": _tools, "messages": _msgs})["content"][0]
    _deny = {"type": "tool_result", "tool_use_id": _block.get("id"), "is_error": True,
             "content": "PreToolUse:Bash hook error: [scout:kb-read-gate] denied"}
    _post({"model": "m", "tools": _tools, "messages": _msgs + [{"role": "assistant", "content": [_block]},
                                                             {"role": "user", "content": [_deny]}]})
    sys.exit(0)
"""
