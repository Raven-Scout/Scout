"""Self-update e2e: answer Scout's two GitHub reads from the local fixture, in any engine version.

`scoutctl self-update` reads the `stable` manifest from raw.githubusercontent.com and the release
from api.github.com, at URLs fixed in the engine. The installed (older) engine can't be given a
new seam, so this file, on PYTHONPATH inside the container only, wraps `urllib.request.urlopen`:

- raw.githubusercontent.com/Raven-Scout/Scout/<ref>/<path> → `git show <ref>:<path>` in $SCOUT_E2E_REMOTE
- api.github.com/repos/Raven-Scout/Scout/releases/tags/<tag> → $SCOUT_E2E_STATE/releases/<tag>.json, else 404
- a file named `offline` in $SCOUT_E2E_STATE makes both raise URLError (a network outage)

Every intercepted call is logged to $SCOUT_E2E_STATE/http-calls.jsonl. Other URLs pass through.
Inert unless SCOUT_E2E_REMOTE is set.
"""

import os

if os.environ.get("SCOUT_E2E_REMOTE"):
    import email.message
    import io
    import json
    import subprocess
    import time
    import urllib.error
    import urllib.request

    _REAL_URLOPEN = urllib.request.urlopen
    _RAW = "https://raw.githubusercontent.com/Raven-Scout/Scout/"
    _API = "https://api.github.com/repos/Raven-Scout/Scout/releases/tags/"

    def _state() -> str:
        return os.environ.get("SCOUT_E2E_STATE", "/tmp")

    def _log(url: str, status: object) -> None:
        os.makedirs(_state(), exist_ok=True)
        with open(os.path.join(_state(), "http-calls.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), "url": url, "status": status}) + "\n")

    def _not_found(url: str) -> urllib.error.HTTPError:
        _log(url, 404)
        return urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)

    def _e2e_urlopen(req, *args, **kwargs):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        if not (url.startswith(_RAW) or url.startswith(_API)):
            return _REAL_URLOPEN(req, *args, **kwargs)
        if os.path.exists(os.path.join(_state(), "offline")):
            _log(url, "offline")
            raise urllib.error.URLError("e2e: network is down")
        if url.startswith(_RAW):
            ref, _, path = url[len(_RAW) :].partition("/")
            proc = subprocess.run(
                ["git", "--git-dir", os.environ["SCOUT_E2E_REMOTE"], "show", f"{ref}:{path}"],
                capture_output=True,
            )
            if proc.returncode != 0:
                raise _not_found(url)
            body = proc.stdout
        else:
            release = os.path.join(_state(), "releases", url[len(_API) :] + ".json")
            if not os.path.exists(release):
                raise _not_found(url)
            with open(release, "rb") as fh:
                body = fh.read()
        _log(url, 200)
        return io.BytesIO(body)

    urllib.request.urlopen = _e2e_urlopen
